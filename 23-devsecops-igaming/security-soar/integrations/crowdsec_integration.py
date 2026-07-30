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
CrowdSec Integration for AcmeToCasino SOAR System.

Provides bi-directional integration with the CrowdSec Local API (LAPI):

  - Pull active ban/captcha decisions and sync them to the local firewall
    (via waf_auto_block.py or direct iptables/ipset management)
  - Push locally detected threats back to CrowdSec as signals for
    community threat intelligence sharing
  - Maintain a local Redis cache of active CrowdSec decisions for fast
    inline lookups (used by the threat detector without LAPI round-trips)
  - Periodic blocklist synchronization with convergence detection

Usage:
    # Start the sync daemon (pull + push loop):
    python crowdsec_integration.py --config /etc/soar/config.yml daemon

    # One-shot pull and sync:
    python crowdsec_integration.py --config /etc/soar/config.yml pull

    # Push a local detection to CrowdSec:
    python crowdsec_integration.py --config /etc/soar/config.yml push \\
        --ip 1.2.3.4 --scenario crowdsecurity/http-bf --duration 4h

    # List active decisions from LAPI:
    python crowdsec_integration.py --config /etc/soar/config.yml list-decisions

    # Show local Redis decision cache stats:
    python crowdsec_integration.py --config /etc/soar/config.yml cache-stats
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import redis
import yaml


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
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("crowdsec_integration")


# ---------------------------------------------------------------------------
# CrowdSec LAPI client
# ---------------------------------------------------------------------------

class CrowdSecLAPIClient:
    """
    HTTP client for the CrowdSec Local API (LAPI).

    Implements the LAPI v1 REST interface for bouncer (decision consumer)
    and watcher (signal producer) operations.

    Args:
        lapi_url:  Base URL of the CrowdSec LAPI (e.g. "http://crowdsec:8080").
        api_key:   Bouncer API key issued by ``cscli bouncers add``.
        timeout:   HTTP request timeout in seconds.
    """

    _BOUNCER_AGENT = "AcmeToCasino-SOAR-Bouncer/1.0"

    def __init__(self, lapi_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._base = lapi_url.rstrip("/")
        self._headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "User-Agent": self._BOUNCER_AGENT,
        }
        self._timeout = timeout

    def _request(self, method: str, path: str, body: bytes | None = None) -> Any:
        """
        Perform an HTTP request against the LAPI.

        Args:
            method: HTTP verb (GET, POST, DELETE).
            path:   URL path relative to the LAPI base URL.
            body:   Optional JSON request body.

        Returns:
            Parsed JSON response (dict or list), or None for empty responses.

        Raises:
            RuntimeError: On HTTP errors or connection failures.
        """
        url = f"{self._base}{path}"
        req = urllib.request.Request(url, data=body, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read(2048).decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(
                f"LAPI HTTP {exc.code} on {method} {path}: {body_text[:200]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"LAPI connection error {method} {path}: {exc}") from exc

    # --- Decision APIs -------------------------------------------------------

    def get_decisions(
        self,
        ip: str | None = None,
        scope: str = "Ip",
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve active decisions from the CrowdSec LAPI.

        Args:
            ip:          Filter by specific IP or CIDR (optional).
            scope:       Decision scope: Ip | Range | Country | As.
            type_filter: Decision type: ban | captcha | throttle (optional).

        Returns:
            List of decision objects as returned by LAPI.
        """
        params: dict[str, str] = {"scope": scope}
        if ip:
            params["ip"] = ip
        if type_filter:
            params["type"] = type_filter
        query = urllib.parse.urlencode(params)
        result = self._request("GET", f"/v1/decisions?{query}")
        if result is None:
            return []
        return result if isinstance(result, list) else []

    def get_decisions_stream(self, startup: bool = False) -> dict[str, list[dict[str, Any]]]:
        """
        Use the streaming endpoint to get new/deleted decisions since last poll.

        This is the preferred polling method for ongoing sync: it returns
        only the delta (new decisions + expired/deleted decisions).

        Args:
            startup: When True, return all current decisions (full sync).

        Returns:
            Dict with keys "new" and "deleted", each containing a list of decision dicts.
        """
        path = f"/v1/decisions/stream?startup={'true' if startup else 'false'}"
        result = self._request("GET", path)
        if result is None:
            return {"new": [], "deleted": []}
        return {
            "new": result.get("new") or [],
            "deleted": result.get("deleted") or [],
        }

    def delete_decision(self, decision_id: int) -> None:
        """
        Delete a decision from the LAPI.

        Args:
            decision_id: Integer ID of the decision to delete.
        """
        self._request("DELETE", f"/v1/decisions/{decision_id}")

    # --- Alert / signal APIs -------------------------------------------------

    def push_alert(
        self,
        source_ip: str,
        scenario: str,
        message: str,
        start_at: datetime | None = None,
        stop_at: datetime | None = None,
        capacity: int = 0,
        leak_speed: str = "0s",
        simulated: bool = False,
    ) -> None:
        """
        Push a locally detected attack signal to CrowdSec as a LAPI alert.

        This contributes to community threat intelligence. The signal is
        associated with an existing CrowdSec scenario name.

        Args:
            source_ip:   Attacker's IP address.
            scenario:    CrowdSec scenario name (e.g. "crowdsecurity/http-bf").
            message:     Human-readable description of the alert.
            start_at:    When the attack began (default: now).
            stop_at:     When the attack ended (default: now).
            capacity:    Bucket capacity (0 = not applicable).
            leak_speed:  Bucket leak speed (used for rate-based scenarios).
            simulated:   Mark as simulated (won't count toward real decisions).
        """
        now = datetime.now(tz=timezone.utc)
        ts_fmt = "%Y-%m-%dT%H:%M:%SZ"
        alert_body = [
            {
                "scenario": scenario,
                "scenario_hash": "",
                "scenario_version": "1.0",
                "message": message,
                "events_count": 1,
                "start_at": (start_at or now).strftime(ts_fmt),
                "stop_at": (stop_at or now).strftime(ts_fmt),
                "capacity": capacity,
                "leakspeed": leak_speed,
                "simulated": simulated,
                "source": {
                    "scope": "Ip",
                    "value": source_ip,
                    "ip": source_ip,
                },
                "events": [
                    {
                        "timestamp": now.strftime(ts_fmt),
                        "meta": [{"key": "source_ip", "value": source_ip}],
                    }
                ],
                "meta": [
                    {"key": "pushed_by", "value": "AcmeToCasino-SOAR"},
                ],
                "decisions": [],
            }
        ]
        self._request("POST", "/v1/alerts", body=json.dumps(alert_body).encode("utf-8"))

    # --- Bouncer decision check (single IP) ----------------------------------

    def is_banned(self, ip: str) -> bool:
        """
        Check whether a specific IP has an active ban decision.

        Args:
            ip: IPv4 or IPv6 address string.

        Returns:
            True if LAPI has at least one active "ban" decision for the IP.
        """
        decisions = self.get_decisions(ip=ip, type_filter="ban")
        return len(decisions) > 0

    # --- LAPI health check ---------------------------------------------------

    def heartbeat(self) -> bool:
        """Return True if the LAPI heartbeat endpoint responds successfully."""
        try:
            self._request("GET", "/v1/heartbeat")
            return True
        except RuntimeError:
            return False


# ---------------------------------------------------------------------------
# Local decision cache (Redis)
# ---------------------------------------------------------------------------

class DecisionCache:
    """
    Redis-backed cache of active CrowdSec decisions for low-latency lookups.

    The cache mirrors the LAPI's active decisions so that inline IP checks
    (e.g. from the threat detector) don't require a LAPI round-trip.

    Args:
        redis_client: Connected redis.Redis instance.
        key_prefix:   Namespace prefix for all cache keys.
        default_ttl:  Fallback TTL (seconds) when no explicit duration is given.
    """

    def __init__(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        key_prefix: str = "soar:crowdsec:",
        default_ttl: int = 3600,
    ) -> None:
        self._r = redis_client
        self._prefix = key_prefix
        self._default_ttl = default_ttl

    def _k(self, ip: str) -> str:
        return f"{self._prefix}ban:{ip}"

    def add(self, ip: str, decision: dict[str, Any]) -> None:
        """
        Cache a ban decision for an IP.

        Args:
            ip:       IP address string.
            decision: Decision object from LAPI.
        """
        key = self._k(ip)
        self._r.hset(key, mapping={
            "scenario": decision.get("scenario", ""),
            "type": decision.get("type", "ban"),
            "decision_id": str(decision.get("id", "")),
            "origin": decision.get("origin", ""),
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
        })
        # Parse duration and compute TTL
        ttl = self._parse_duration(str(decision.get("duration", "1h")))
        self._r.expire(key, ttl)

    def remove(self, ip: str) -> None:
        """Remove an IP from the ban cache."""
        self._r.delete(self._k(ip))

    def is_banned(self, ip: str) -> bool:
        """Return True if the IP has an active cached ban decision."""
        return bool(self._r.exists(self._k(ip)))

    def get_decision(self, ip: str) -> dict[str, str] | None:
        """Return the cached decision data for an IP, or None."""
        result = self._r.hgetall(self._k(ip))
        return result if result else None

    def count_banned_ips(self) -> int:
        """Return the number of currently banned IPs in the cache."""
        pattern = f"{self._prefix}ban:*"
        count = sum(1 for _ in self._r.scan_iter(pattern))
        return count

    @staticmethod
    def _parse_duration(duration_str: str) -> int:
        """
        Convert a CrowdSec duration string to seconds.

        Supports formats like: "4h", "24h", "7d", "30m", "3600s".

        Args:
            duration_str: Duration string from LAPI decision.

        Returns:
            Duration in seconds (minimum 60, maximum 604800 / 7 days).
        """
        m = re.fullmatch(r"(\d+)([smhd])", duration_str.strip().lower())
        if not m:
            return 3600  # default 1 hour
        value, unit = int(m.group(1)), m.group(2)
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        seconds = value * multipliers[unit]
        return max(60, min(seconds, 604800))


# ---------------------------------------------------------------------------
# Firewall synchronizer
# ---------------------------------------------------------------------------

class FirewallSyncer:
    """
    Synchronizes CrowdSec ban decisions with the local Linux firewall.

    Supports iptables/ipset backends. On AWS deployments, delegates to
    waf_auto_block.py instead.

    Args:
        backend:     "iptables", "ipset", or "aws_waf".
        chain:       iptables chain name for SOAR rules.
        waf_script:  Path to waf_auto_block.py for aws_waf backend.
        dry_run:     When True, log actions without executing.
    """

    def __init__(
        self,
        backend: str = "iptables",
        chain: str = "SOAR_BLOCK",
        waf_script: str = "/opt/soar/aws-waf/waf_auto_block.py",
        dry_run: bool = False,
    ) -> None:
        self._backend = backend
        self._chain = chain
        self._waf_script = waf_script
        self._dry_run = dry_run

    def block_ip(self, ip: str, comment: str = "CrowdSec SOAR") -> bool:
        """
        Add an IP block rule to the local firewall.

        Args:
            ip:      IPv4 or IPv6 CIDR to block.
            comment: Rule comment for identification.

        Returns:
            True on success.
        """
        if self._dry_run:
            log.info("[DRY-RUN] Would block IP %s via %s", ip, self._backend)
            return True

        if self._backend == "iptables":
            return self._iptables_block(ip, comment)
        if self._backend == "ipset":
            return self._ipset_block(ip)
        if self._backend == "aws_waf":
            return self._waf_block(ip)
        log.warning("Unknown firewall backend: %s", self._backend)
        return False

    def unblock_ip(self, ip: str) -> bool:
        """
        Remove an IP block rule from the local firewall.

        Args:
            ip: IPv4 or IPv6 CIDR to unblock.

        Returns:
            True on success.
        """
        if self._dry_run:
            log.info("[DRY-RUN] Would unblock IP %s via %s", ip, self._backend)
            return True

        if self._backend == "iptables":
            return self._iptables_unblock(ip)
        if self._backend == "ipset":
            return self._ipset_unblock(ip)
        if self._backend == "aws_waf":
            return self._waf_unblock(ip)
        return False

    def _iptables_block(self, ip: str, comment: str) -> bool:
        cmd = [
            "iptables", "-I", self._chain, "1",
            "-s", ip, "-j", "DROP",
            "-m", "comment", "--comment", comment,
        ]
        return self._run(cmd)

    def _iptables_unblock(self, ip: str) -> bool:
        cmd = ["iptables", "-D", self._chain, "-s", ip, "-j", "DROP"]
        return self._run(cmd)

    def _ipset_block(self, ip: str) -> bool:
        cmd = ["ipset", "add", "soar-blocklist", ip, "-exist"]
        return self._run(cmd)

    def _ipset_unblock(self, ip: str) -> bool:
        cmd = ["ipset", "del", "soar-blocklist", ip, "-exist"]
        return self._run(cmd)

    def _waf_block(self, ip: str) -> bool:
        cmd = ["python3", self._waf_script, "block", "--ip", f"{ip}/32", "--scope", "REGIONAL"]
        return self._run(cmd)

    def _waf_unblock(self, ip: str) -> bool:
        cmd = ["python3", self._waf_script, "unblock", "--ip", f"{ip}/32", "--scope", "REGIONAL"]
        return self._run(cmd)

    def _run(self, cmd: list[str]) -> bool:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            if result.returncode != 0:
                log.warning("Firewall command failed: %s | stderr: %s", " ".join(cmd), result.stderr[:200])
                return False
            return True
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.error("Firewall command error: %s | %s", " ".join(cmd), exc)
            return False


# ---------------------------------------------------------------------------
# Sync daemon
# ---------------------------------------------------------------------------

class CrowdSecSyncDaemon:
    """
    Polls CrowdSec LAPI for decision updates and synchronizes with the
    local firewall and Redis decision cache.

    On startup, performs a full sync. Subsequent polls use the streaming
    endpoint to fetch only delta decisions.

    Args:
        client:          CrowdSecLAPIClient instance.
        cache:           DecisionCache instance.
        syncer:          FirewallSyncer instance.
        pull_interval:   Seconds between LAPI polls.
        push_detections: When True, push local SOAR alerts to CrowdSec.
    """

    def __init__(
        self,
        client: CrowdSecLAPIClient,
        cache: DecisionCache,
        syncer: FirewallSyncer,
        pull_interval: int = 60,
        push_detections: bool = True,
    ) -> None:
        self._client = client
        self._cache = cache
        self._syncer = syncer
        self._pull_interval = pull_interval
        self._push_detections = push_detections
        self._running = False

    def run(self) -> None:
        """Start the sync daemon loop. Blocks until KeyboardInterrupt."""
        self._running = True
        log.info("CrowdSec sync daemon starting – full sync on startup")

        # Full sync on startup to pick up decisions from before this process started
        self._pull(startup=True)

        while self._running:
            time.sleep(self._pull_interval)
            if not self._running:
                break
            try:
                self._pull(startup=False)
            except RuntimeError as exc:
                log.error("LAPI pull failed: %s – will retry in %ds", exc, self._pull_interval)

    def stop(self) -> None:
        """Signal the daemon to stop."""
        self._running = False

    def _pull(self, startup: bool = False) -> None:
        """Fetch decisions from LAPI and apply them to the firewall + cache."""
        stream = self._client.get_decisions_stream(startup=startup)
        new_decisions = stream.get("new") or []
        deleted_decisions = stream.get("deleted") or []

        for decision in new_decisions:
            ip = decision.get("value", "")
            if not ip:
                continue
            decision_type = decision.get("type", "ban")
            if decision_type == "ban":
                self._cache.add(ip, decision)
                blocked = self._syncer.block_ip(ip, f"CrowdSec:{decision.get('scenario','')}")
                log.info(
                    "BLOCK ip=%s scenario=%s origin=%s firewall_ok=%s",
                    ip,
                    decision.get("scenario", ""),
                    decision.get("origin", ""),
                    blocked,
                )

        for decision in deleted_decisions:
            ip = decision.get("value", "")
            if not ip:
                continue
            self._cache.remove(ip)
            unblocked = self._syncer.unblock_ip(ip)
            log.info(
                "UNBLOCK ip=%s scenario=%s firewall_ok=%s",
                ip,
                decision.get("scenario", ""),
                unblocked,
            )

        if new_decisions or deleted_decisions:
            log.info(
                "Sync complete: +%d new, -%d deleted | cache_total=%d",
                len(new_decisions),
                len(deleted_decisions),
                self._cache.count_banned_ips(),
            )

    def push_alert_from_soar(self, soar_alert: dict[str, Any]) -> None:
        """
        Convert a SOAR alert dict into a CrowdSec signal and push to LAPI.

        Args:
            soar_alert: Alert dict from the SOAR threat detector.
        """
        if not self._push_detections:
            return

        ip = soar_alert.get("source_ip", "")
        if not ip or ip == "0.0.0.0":
            return

        # Map SOAR alert types to CrowdSec scenario names
        alert_type_scenario_map = {
            "brute_force": "crowdsecurity/http-bf",
            "brute_force_auto_block": "crowdsecurity/http-bf",
            "ddos_rate_ip": "crowdsecurity/http-dos-by-ip",
            "ddos_global_rate": "crowdsecurity/http-dos",
            "slowloris": "crowdsecurity/http-slowloris",
            "sql_injection": "crowdsecurity/http-sqli",
            "xss": "crowdsecurity/http-xss-probing",
            "command_injection": "crowdsecurity/http-cmdi",
            "bot_detected": "crowdsecurity/http-crawl",
            "ato_new_country": "crowdsecurity/http-sensitive-files",
        }
        alert_type = soar_alert.get("alert_type", "")
        scenario = alert_type_scenario_map.get(alert_type, "crowdsecurity/http-generic-attack")

        try:
            self._client.push_alert(
                source_ip=ip,
                scenario=scenario,
                message=soar_alert.get("description", f"SOAR detection: {alert_type}"),
            )
            log.info(
                "Pushed SOAR alert to CrowdSec: ip=%s scenario=%s",
                ip,
                scenario,
            )
        except RuntimeError as exc:
            log.warning("Failed to push alert to CrowdSec: %s", exc)


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


def _build_components(cfg: dict[str, Any]) -> tuple[CrowdSecLAPIClient, DecisionCache, FirewallSyncer]:
    """Construct LAPI client, cache, and firewall syncer from config."""
    cs_cfg = cfg.get("responders", {}).get("crowdsec", {})
    r_cfg = cfg.get("redis", {})
    fw_cfg = cfg.get("responders", {}).get("local_firewall", {})

    client = CrowdSecLAPIClient(
        lapi_url=cs_cfg.get("lapi_url", "http://crowdsec:8080"),
        api_key=cs_cfg.get("api_key", ""),
    )

    r = redis.Redis(
        host=r_cfg.get("host", "localhost"),
        port=int(r_cfg.get("port", 6379)),
        db=int(r_cfg.get("db", 0)),
        password=r_cfg.get("password", "") or None,
        decode_responses=True,
    )
    cache = DecisionCache(
        redis_client=r,
        key_prefix=r_cfg.get("key_prefix", "soar:") + "crowdsec:",
    )

    syncer = FirewallSyncer(
        backend=fw_cfg.get("backend", "iptables"),
        chain=fw_cfg.get("chain", "SOAR_BLOCK"),
        dry_run=str(cfg.get("responders", {}).get("aws_waf", {}).get("dry_run", "false")).lower() in ("true", "1"),
    )

    return client, cache, syncer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR CrowdSec integration",
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

    # daemon
    sub.add_parser("daemon", help="Start the continuous LAPI sync daemon")

    # pull
    sub.add_parser("pull", help="One-shot pull and firewall sync")

    # push
    p_push = sub.add_parser("push", help="Push a local detection to CrowdSec")
    p_push.add_argument("--ip", required=True, help="Attacker IP address")
    p_push.add_argument(
        "--scenario",
        default="crowdsecurity/http-generic-attack",
        help="CrowdSec scenario name",
    )
    p_push.add_argument("--duration", default="4h", help="Signal duration (e.g. 4h, 24h)")
    p_push.add_argument("--message", default="", help="Human-readable description")

    # list-decisions
    p_list = sub.add_parser("list-decisions", help="List active LAPI decisions")
    p_list.add_argument("--ip", default=None, help="Filter by IP address")
    p_list.add_argument("--type", default=None, choices=["ban", "captcha", "throttle"])

    # cache-stats
    sub.add_parser("cache-stats", help="Show Redis decision cache statistics")

    # health-check
    sub.add_parser("health-check", help="Check CrowdSec LAPI availability")

    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))

    client, cache, syncer = _build_components(cfg)
    cs_cfg = cfg.get("responders", {}).get("crowdsec", {})

    try:
        if args.command == "daemon":
            daemon = CrowdSecSyncDaemon(
                client=client,
                cache=cache,
                syncer=syncer,
                pull_interval=int(cs_cfg.get("pull_interval_seconds", 60)),
                push_detections=str(cs_cfg.get("push_detections", "true")).lower() in ("true", "1"),
            )
            try:
                daemon.run()
            except KeyboardInterrupt:
                log.info("Daemon stopped")

        elif args.command == "pull":
            daemon = CrowdSecSyncDaemon(client, cache, syncer)
            daemon._pull(startup=True)

        elif args.command == "push":
            client.push_alert(
                source_ip=args.ip,
                scenario=args.scenario,
                message=args.message or f"Manual SOAR push: {args.scenario} from {args.ip}",
            )
            log.info("Pushed alert to CrowdSec: ip=%s scenario=%s", args.ip, args.scenario)

        elif args.command == "list-decisions":
            decisions = client.get_decisions(ip=args.ip, type_filter=args.type)
            if not decisions:
                log.info("No active decisions found")
            for d in decisions:
                print(json.dumps(d))

        elif args.command == "cache-stats":
            banned_count = cache.count_banned_ips()
            print(json.dumps({"banned_ips_in_cache": banned_count}))

        elif args.command == "health-check":
            ok = client.heartbeat()
            log.info("CrowdSec LAPI health: %s", "ok" if ok else "fail")
            sys.exit(0 if ok else 1)

    except RuntimeError as exc:
        log.error("Command '%s' failed: %s", args.command, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
