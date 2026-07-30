#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
GeoIP Enrichment Module for AcmeToCasino SOAR System.

Adds geographic context (country, city, ASN, organization) to IP addresses
in normalized events. Detects VPN, proxy, Tor exit node, and datacenter IPs.

Reads events from stdin (one JSON object per line) or from a Redis stream,
enriches each event, then writes to stdout or Redis.

Usage:
    # Enrich a single IP on the command line:
    python geoip_enricher.py --config /etc/soar/config.yml lookup 8.8.8.8

    # Stream mode (reads JSONL from stdin, writes enriched JSONL to stdout):
    cat events.jsonl | python geoip_enricher.py --config /etc/soar/config.yml stream

    # Redis stream mode (reads soar:raw-events, writes soar:events):
    python geoip_enricher.py --config /etc/soar/config.yml redis-stream
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# MaxMind geoip2 – optional import with graceful degradation
try:
    import geoip2.database
    import geoip2.errors
    _GEOIP2_AVAILABLE = True
except ImportError:
    _GEOIP2_AVAILABLE = False
    geoip2 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str, level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)  # stderr so stdout stays clean for piping
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("geoip_enricher")


# ---------------------------------------------------------------------------
# LRU Cache
# ---------------------------------------------------------------------------

class LRUCache:
    """
    Simple thread-unsafe LRU cache backed by an OrderedDict.

    For the enricher's single-threaded stream mode this is sufficient.
    For concurrent use, wrap access with a threading.Lock.

    Args:
        capacity: Maximum number of entries.
        ttl:      Time-to-live for entries in seconds (0 = no TTL).
    """

    def __init__(self, capacity: int = 65536, ttl: int = 3600) -> None:
        self._cache: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl

    def get(self, key: str) -> dict[str, Any] | None:
        """Return cached value or None if missing/expired."""
        if key not in self._cache:
            return None
        value, inserted_at = self._cache[key]
        if self._ttl > 0 and (time.monotonic() - inserted_at) > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Insert or update an entry, evicting the LRU entry when at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, time.monotonic())
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Datacenter CIDR loader
# ---------------------------------------------------------------------------

def _load_datacenter_cidrs(path: str) -> list[tuple[Any, str]]:
    """
    Load a CSV/text file mapping datacenter IP ranges to provider names.

    Expected format (one entry per line, header optional):
        CIDR,ProviderName
        3.0.0.0/8,Amazon AWS
        35.192.0.0/12,Google Cloud

    Lines starting with '#' are treated as comments.

    Args:
        path: Absolute path to the datacenter CIDR file.

    Returns:
        List of (network_object, provider_name) tuples.
    """
    entries: list[tuple[Any, str]] = []
    if not path or not Path(path).exists():
        log.debug("Datacenter CIDR file not found: %s", path)
        return entries
    try:
        with open(path, encoding="utf-8") as fh:
            reader = csv.reader(fh)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                cidr_str = row[0].strip()
                provider = row[1].strip() if len(row) > 1 else "unknown"
                try:
                    network = ipaddress.ip_network(cidr_str, strict=False)
                    entries.append((network, provider))
                except ValueError:
                    log.debug("Skipping invalid CIDR in datacenter list: %s", cidr_str)
    except OSError as exc:
        log.warning("Cannot read datacenter CIDR file %s: %s", path, exc)
    log.info("Loaded %d datacenter CIDR entries from %s", len(entries), path)
    return entries


def _load_vpn_cidrs(path: str) -> list[Any]:
    """
    Load a plain-text file of VPN/proxy CIDRs (one per line).

    Lines starting with '#' are treated as comments.

    Args:
        path: Absolute path to the VPN CIDR list.

    Returns:
        List of ip_network objects.
    """
    entries: list[Any] = []
    if not path or not Path(path).exists():
        log.debug("VPN CIDR file not found: %s", path)
        return entries
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    entries.append(ipaddress.ip_network(line, strict=False))
                except ValueError:
                    log.debug("Skipping invalid VPN CIDR: %s", line)
    except OSError as exc:
        log.warning("Cannot read VPN CIDR file %s: %s", path, exc)
    log.info("Loaded %d VPN/proxy CIDR entries from %s", len(entries), path)
    return entries


def _ip_in_networks(ip_str: str, networks: list[Any]) -> bool:
    """Return True if *ip_str* falls within any of the given networks."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _datacenter_name_for_ip(ip_str: str, entries: list[tuple[Any, str]]) -> str | None:
    """Return the datacenter provider name if *ip_str* matches an entry, else None."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    for network, provider in entries:
        if addr in network:
            return provider
    return None


# ---------------------------------------------------------------------------
# GeoIP enricher core
# ---------------------------------------------------------------------------

class GeoIPEnricher:
    """
    Enriches IP addresses with geographic and threat intelligence metadata.

    Requires MaxMind GeoLite2 databases to be present on disk. If the
    ``geoip2`` Python package is not installed, enrichment fields are set
    to empty strings and VPN/datacenter detection falls back to local lists.

    Args:
        city_db_path:   Path to GeoLite2-City.mmdb.
        asn_db_path:    Path to GeoLite2-ASN.mmdb.
        anon_db_path:   Path to GeoLite2-Anonymous-IP.mmdb (optional).
        vpn_list_path:  Path to plain-text VPN/proxy CIDR list.
        dc_list_path:   Path to CSV datacenter CIDR list.
        cache_size:     LRU cache capacity.
        cache_ttl:      LRU cache TTL in seconds.
    """

    # Private IPs that should never be enriched
    _PRIVATE_NETWORKS = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
    ]

    def __init__(
        self,
        city_db_path: str = "",
        asn_db_path: str = "",
        anon_db_path: str = "",
        vpn_list_path: str = "",
        dc_list_path: str = "",
        cache_size: int = 65536,
        cache_ttl: int = 3600,
    ) -> None:
        self._cache = LRUCache(capacity=cache_size, ttl=cache_ttl)
        self._city_reader: Any = None
        self._asn_reader: Any = None
        self._anon_reader: Any = None
        self._vpn_cidrs: list[Any] = []
        self._dc_entries: list[tuple[Any, str]] = []

        if not _GEOIP2_AVAILABLE:
            log.warning(
                "geoip2 package not installed. "
                "Install with: pip install geoip2  "
                "Geographic enrichment will be limited to local CIDR lists."
            )

        if _GEOIP2_AVAILABLE:
            if city_db_path and Path(city_db_path).exists():
                self._city_reader = geoip2.database.Reader(city_db_path)
                log.info("Loaded GeoLite2-City: %s", city_db_path)
            else:
                log.warning("GeoLite2-City database not found: %s", city_db_path)

            if asn_db_path and Path(asn_db_path).exists():
                self._asn_reader = geoip2.database.Reader(asn_db_path)
                log.info("Loaded GeoLite2-ASN: %s", asn_db_path)
            else:
                log.warning("GeoLite2-ASN database not found: %s", asn_db_path)

            if anon_db_path and Path(anon_db_path).exists():
                self._anon_reader = geoip2.database.Reader(anon_db_path)
                log.info("Loaded GeoLite2-Anonymous-IP: %s", anon_db_path)

        self._vpn_cidrs = _load_vpn_cidrs(vpn_list_path)
        self._dc_entries = _load_datacenter_cidrs(dc_list_path)

    def close(self) -> None:
        """Release MaxMind database file handles."""
        for reader in (self._city_reader, self._asn_reader, self._anon_reader):
            if reader is not None:
                reader.close()

    def _is_private(self, ip_str: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip_str)
            return any(addr in net for net in self._PRIVATE_NETWORKS)
        except ValueError:
            return False

    def _lookup_maxmind(self, ip_str: str) -> dict[str, Any]:
        """
        Perform a MaxMind lookup for a single IP address.

        Returns a dict with geographic and ASN fields. Missing or
        unavailable database responses result in empty string defaults.

        Args:
            ip_str: IPv4 or IPv6 address string.

        Returns:
            Dictionary with keys: country_code, country_name, city, subdivision,
            latitude, longitude, timezone, asn, asn_org, is_vpn, is_proxy, is_tor,
            is_hosting, is_anonymous, is_datacenter, datacenter_name.
        """
        result: dict[str, Any] = {
            "country_code": "",
            "country_name": "",
            "city": "",
            "subdivision": "",
            "latitude": None,
            "longitude": None,
            "timezone": "",
            "asn": 0,
            "asn_org": "",
            "is_vpn": False,
            "is_proxy": False,
            "is_tor": False,
            "is_hosting": False,
            "is_anonymous": False,
            "is_datacenter": False,
            "datacenter_name": "",
        }

        # City / geographic lookup
        if _GEOIP2_AVAILABLE and self._city_reader:
            try:
                city_resp = self._city_reader.city(ip_str)
                result["country_code"] = city_resp.country.iso_code or ""
                result["country_name"] = city_resp.country.name or ""
                result["city"] = city_resp.city.name or ""
                result["subdivision"] = (
                    city_resp.subdivisions.most_specific.name or ""
                    if city_resp.subdivisions else ""
                )
                if city_resp.location.latitude is not None:
                    result["latitude"] = round(city_resp.location.latitude, 4)
                    result["longitude"] = round(city_resp.location.longitude, 4)
                result["timezone"] = city_resp.location.time_zone or ""
            except geoip2.errors.AddressNotFoundError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("City lookup failed for %s: %s", ip_str, exc)

        # ASN lookup
        if _GEOIP2_AVAILABLE and self._asn_reader:
            try:
                asn_resp = self._asn_reader.asn(ip_str)
                result["asn"] = asn_resp.autonomous_system_number or 0
                result["asn_org"] = asn_resp.autonomous_system_organization or ""
            except geoip2.errors.AddressNotFoundError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("ASN lookup failed for %s: %s", ip_str, exc)

        # Anonymous IP database (VPN/proxy/Tor/hosting)
        if _GEOIP2_AVAILABLE and self._anon_reader:
            try:
                anon_resp = self._anon_reader.anonymous_ip(ip_str)
                result["is_vpn"] = bool(anon_resp.is_anonymous_vpn)
                result["is_proxy"] = bool(anon_resp.is_public_proxy)
                result["is_tor"] = bool(anon_resp.is_tor_exit_node)
                result["is_hosting"] = bool(anon_resp.is_hosting_provider)
                result["is_anonymous"] = bool(anon_resp.is_anonymous)
            except geoip2.errors.AddressNotFoundError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("Anonymous IP lookup failed for %s: %s", ip_str, exc)

        # Fall back to local CIDR list for VPN detection
        if not result["is_vpn"] and not result["is_proxy"] and not result["is_tor"]:
            if _ip_in_networks(ip_str, self._vpn_cidrs):
                result["is_vpn"] = True

        # Datacenter detection via local CIDR list
        dc_name = _datacenter_name_for_ip(ip_str, self._dc_entries)
        if dc_name:
            result["is_datacenter"] = True
            result["datacenter_name"] = dc_name
        elif result["is_hosting"]:
            result["is_datacenter"] = True
            result["datacenter_name"] = result.get("asn_org", "unknown")

        return result

    def enrich(self, ip_str: str) -> dict[str, Any]:
        """
        Enrich a single IP address with GeoIP metadata.

        Results are cached by IP. Private/loopback addresses return a stub
        dict with ``is_private=True`` and empty geographic fields.

        Args:
            ip_str: IPv4 or IPv6 address string.

        Returns:
            Dict with GeoIP enrichment fields merged and ready to
            update a normalized event.
        """
        ip_str = ip_str.strip()

        if self._is_private(ip_str):
            return {
                "is_private": True,
                "country_code": "",
                "country_name": "",
                "city": "",
                "subdivision": "",
                "latitude": None,
                "longitude": None,
                "timezone": "",
                "asn": 0,
                "asn_org": "",
                "is_vpn": False,
                "is_proxy": False,
                "is_tor": False,
                "is_hosting": False,
                "is_anonymous": False,
                "is_datacenter": False,
                "datacenter_name": "",
            }

        cached = self._cache.get(ip_str)
        if cached is not None:
            return cached

        result = self._lookup_maxmind(ip_str)
        result["is_private"] = False
        self._cache.set(ip_str, result)
        return result

    def enrich_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Enrich a normalized SOAR event with GeoIP data.

        The ``source_ip`` field is used for lookup. All enrichment fields
        are merged into the top-level event dict with a ``geo_`` prefix to
        avoid collisions with existing fields.

        Args:
            event: Normalized event dict (modified in-place and returned).

        Returns:
            The same event dict with GeoIP fields added.
        """
        ip = event.get("source_ip", "")
        if not ip:
            return event

        geo = self.enrich(ip)
        # Merge with geo_ prefix so enrichment fields are clearly namespaced
        for k, v in geo.items():
            event[f"geo_{k}"] = v

        # Also set top-level convenience aliases used by detectors
        if not event.get("country_code"):
            event["country_code"] = geo.get("country_code", "")
        if not event.get("country"):
            event["country"] = geo.get("country_name", "")
        event["is_vpn"] = geo.get("is_vpn", False)
        event["is_proxy"] = geo.get("is_proxy", False)
        event["is_tor"] = geo.get("is_tor", False)
        event["is_datacenter"] = geo.get("is_datacenter", False)
        event["datacenter_name"] = geo.get("datacenter_name", "")

        return event

    def stats(self) -> dict[str, Any]:
        """Return cache statistics for monitoring/debugging."""
        return {
            "cache_size": len(self._cache),
            "cache_capacity": self._cache._capacity,
            "city_db_loaded": self._city_reader is not None,
            "asn_db_loaded": self._asn_reader is not None,
            "anon_db_loaded": self._anon_reader is not None,
            "vpn_cidr_count": len(self._vpn_cidrs),
            "datacenter_cidr_count": len(self._dc_entries),
        }


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

    def _replace(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(2) or "")

    return pattern.sub(_replace, value)


def _deep_resolve(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    except OSError as exc:
        log.error("Cannot read config: %s", exc)
        sys.exit(1)
    except yaml.YAMLError as exc:
        log.error("Invalid YAML: %s", exc)
        sys.exit(1)
    return _deep_resolve(raw)


def _enricher_from_config(cfg: dict[str, Any]) -> GeoIPEnricher:
    """Construct a GeoIPEnricher from the enrichment section of config.yml."""
    enrich_cfg = cfg.get("enrichment", {})
    geo_cfg = enrich_cfg.get("geoip", {})
    vpn_cfg = enrich_cfg.get("vpn_proxy", {})
    dc_cfg = enrich_cfg.get("datacenter", {})

    return GeoIPEnricher(
        city_db_path=geo_cfg.get("db_city_path", ""),
        asn_db_path=geo_cfg.get("db_asn_path", ""),
        anon_db_path=vpn_cfg.get("db_anon_path", ""),
        vpn_list_path=vpn_cfg.get("local_list_path", ""),
        dc_list_path=dc_cfg.get("datacenter_list_path", ""),
        cache_size=int(geo_cfg.get("cache_size", 65536)),
        cache_ttl=int(geo_cfg.get("cache_ttl_seconds", 3600)),
    )


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_lookup(ip: str, enricher: GeoIPEnricher) -> None:
    """Print enrichment data for a single IP address."""
    result = enricher.enrich(ip)
    print(json.dumps({"ip": ip, "enrichment": result}, indent=2))


def cmd_stream(enricher: GeoIPEnricher, infile: Any = None, outfile: Any = None) -> None:
    """
    Read JSONL events from *infile* (default: stdin), enrich each, write to *outfile* (default: stdout).

    Args:
        enricher: GeoIPEnricher instance.
        infile:   File-like object to read from.
        outfile:  File-like object to write to.
    """
    inf = infile or sys.stdin
    outf = outfile or sys.stdout
    count = 0
    for line in inf:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Skipping non-JSON line: %.80s", line)
            continue
        enriched = enricher.enrich_event(event)
        outf.write(json.dumps(enriched) + "\n")
        outf.flush()
        count += 1
        if count % 10000 == 0:
            log.info("Enriched %d events | cache_size=%d", count, len(enricher._cache))

    log.info("Stream enrichment complete: %d events | stats=%s", count, enricher.stats())


def cmd_redis_stream(enricher: GeoIPEnricher, cfg: dict[str, Any]) -> None:
    """
    Consume raw events from Redis stream 'soar:raw-events', enrich, and
    publish to 'soar:events'.

    Args:
        enricher: GeoIPEnricher instance.
        cfg:      Full SOAR configuration dict.
    """
    try:
        import redis as _redis
    except ImportError:
        log.error("redis-py is required for Redis stream mode: pip install redis")
        sys.exit(1)

    r_cfg = cfg.get("redis", {})
    r = _redis.Redis(
        host=r_cfg.get("host", "localhost"),
        port=int(r_cfg.get("port", 6379)),
        db=int(r_cfg.get("db", 0)),
        password=r_cfg.get("password", "") or None,
        decode_responses=True,
    )

    src_stream = "soar:raw-events"
    dst_stream = "soar:events"
    group = "geoip-enricher"
    consumer = "enricher-1"

    try:
        r.xgroup_create(src_stream, group, id="$", mkstream=True)
        log.info("Created consumer group '%s' on stream '%s'", group, src_stream)
    except _redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    log.info("GeoIP enricher ready – %s -> %s", src_stream, dst_stream)
    count = 0

    while True:
        try:
            messages = r.xreadgroup(group, consumer, {src_stream: ">"}, count=100, block=2000)
        except _redis.exceptions.ConnectionError as exc:
            log.error("Redis connection lost: %s – retrying in 5s", exc)
            time.sleep(5)
            continue

        if not messages:
            continue

        pipe = r.pipeline()
        for _stream, entries in messages:
            for msg_id, fields in entries:
                raw = fields.get("data", "")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    r.xack(src_stream, group, msg_id)
                    continue
                enriched = enricher.enrich_event(event)
                pipe.xadd(dst_stream, {"data": json.dumps(enriched)}, maxlen=500000, approximate=True)
                pipe.xack(src_stream, group, msg_id)
                count += 1
        pipe.execute()

        if count % 10000 == 0:
            log.info("Enriched %d events | stats=%s", count, enricher.stats())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR GeoIP enrichment module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="/etc/soar/config.yml",
        help="Path to SOAR YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # lookup
    p_lookup = sub.add_parser("lookup", help="Look up a single IP address")
    p_lookup.add_argument("ip", help="IPv4 or IPv6 address to enrich")

    # stream
    p_stream = sub.add_parser(
        "stream",
        help="Read JSONL events from stdin, enrich, write to stdout",
    )
    p_stream.add_argument(
        "--input",
        metavar="FILE",
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    p_stream.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Output JSONL file (default: stdout)",
    )

    # redis-stream
    sub.add_parser(
        "redis-stream",
        help="Consume from soar:raw-events Redis stream and publish to soar:events",
    )

    # stats
    sub.add_parser("stats", help="Print enricher cache and database statistics")

    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))

    enricher = _enricher_from_config(cfg)

    try:
        if args.command == "lookup":
            cmd_lookup(args.ip, enricher)

        elif args.command == "stream":
            inf = open(args.input, encoding="utf-8") if args.input else None  # noqa: WPS515
            outf = open(args.output, "w", encoding="utf-8") if args.output else None  # noqa: WPS515
            try:
                cmd_stream(enricher, inf, outf)
            finally:
                if inf:
                    inf.close()
                if outf:
                    outf.close()

        elif args.command == "redis-stream":
            cmd_redis_stream(enricher, cfg)

        elif args.command == "stats":
            print(json.dumps(enricher.stats(), indent=2))

    except KeyboardInterrupt:
        log.info("Shutdown signal received")
    finally:
        enricher.close()


if __name__ == "__main__":
    main()
