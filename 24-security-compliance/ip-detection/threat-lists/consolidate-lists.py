#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
IP Threat Intelligence Consolidation Script
============================================
Downloads, validates, deduplicates, and exports IP threat lists for iGaming
fraud and proxy detection.

Sources: Tor exits, VPN ranges, open proxies, datacenter ranges, bot IPs,
         and general abuse/malicious IP feeds.

Outputs:
  - tor-exits.txt          Plain IPv4, one per line
  - vpn-ips.txt            CIDR ranges, one per line
  - proxy-ips.txt          Plain IPv4 (port stripped), one per line
  - datacenter-ranges.txt  CIDR ranges, one per line
  - bot-ips.txt            Plain IPv4, one per line
  - abuse-ips.txt          Plain IPv4/CIDR, one per line
  - consolidated-threats.txt  All IPs/ranges with category annotations
  - redis-load.sh          Redis SADD bulk import script
  - cloudflare-kv-bulk.json   Cloudflare KV bulk upload JSON
  - aws-waf-ipset.json     AWS WAF IP set JSON

Usage:
  python3 consolidate-lists.py [--output-dir /path/to/output] [--cache-dir /path/to/cache]
                               [--cache-ttl-hours 24] [--min-ipsum-score 3]
                               [--no-download] [--verbose]

Cron example (daily at 03:00):
  0 3 * * * /usr/bin/python3 /opt/threat-lists/consolidate-lists.py \
      --output-dir /opt/threat-lists/output \
      --cache-dir /opt/threat-lists/cache \
      >> /var/log/threat-lists-consolidate.log 2>&1
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
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_VERSION = "1.0.0"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CACHE_TTL_HOURS = 24
DEFAULT_MIN_IPSUM_SCORE = 3

# Category names used consistently across all outputs
CAT_TOR = "tor"
CAT_VPN = "vpn"
CAT_PROXY = "proxy"
CAT_DATACENTER = "datacenter"
CAT_BOT = "bot"
CAT_ABUSE = "abuse"

ALL_CATEGORIES = [CAT_TOR, CAT_VPN, CAT_PROXY, CAT_DATACENTER, CAT_BOT, CAT_ABUSE]

# AWS WAF has a hard limit of 10,000 addresses per IP set
AWS_WAF_MAX_ADDRESSES = 10_000

# Cloudflare KV value size limit (25 MB per value, 512 byte key limit)
CF_KV_BATCH_SIZE = 10_000

# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

SOURCES: List[Dict] = [
    # --- TOR EXIT NODES ---
    {
        "id": "tor_project_bulk",
        "url": "https://check.torproject.org/torbulkexitlist",
        "category": CAT_TOR,
        "format": "plain_ip",
        "reliability": 5,
    },
    {
        "id": "firehol_tor_exits",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/tor_exits.ipset",
        "category": CAT_TOR,
        "format": "ipset_hash",
        "reliability": 5,
    },
    {
        "id": "firehol_tor_exits_7d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/tor_exits_7d.ipset",
        "category": CAT_TOR,
        "format": "ipset_hash",
        "reliability": 5,
    },
    {
        "id": "et_tor",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/et_tor.ipset",
        "category": CAT_TOR,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "dan_me_uk_tor",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/dm_tor.ipset",
        "category": CAT_TOR,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "secops_tor",
        "url": "https://raw.githubusercontent.com/SecOps-Institute/Tor-IP-Addresses/master/tor-exit-nodes.lst",
        "category": CAT_TOR,
        "format": "plain_ip",
        "reliability": 3,
    },
    # --- VPN PROVIDER IP RANGES ---
    {
        "id": "x4bnet_vpn_ipv4",
        "url": "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/vpn/ipv4.txt",
        "category": CAT_VPN,
        "format": "cidr",
        "reliability": 5,
    },
    {
        "id": "x4bnet_vpn_ipv6",
        "url": "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/vpn/ipv6.txt",
        "category": CAT_VPN,
        "format": "cidr",
        "reliability": 5,
    },
    # --- DATACENTER / HOSTING IP RANGES ---
    {
        "id": "x4bnet_datacenter_ipv4",
        "url": "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/datacenter/ipv4.txt",
        "category": CAT_DATACENTER,
        "format": "cidr",
        "reliability": 5,
    },
    {
        "id": "jhassine_datacenters",
        "url": "https://raw.githubusercontent.com/jhassine/server-ip-addresses/master/data/datacenters.csv",
        "category": CAT_DATACENTER,
        "format": "csv_cidr",
        "reliability": 4,
    },
    # --- OPEN PROXY LISTS ---
    {
        "id": "mmpx12_http",
        "url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "mmpx12_socks4",
        "url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks4.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "mmpx12_socks5",
        "url": "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "thespeedx_http",
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "thespeedx_socks4",
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "thespeedx_socks5",
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "shiftytr_proxy",
        "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/proxy.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "shiftytr_socks5",
        "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "hookzof_socks5",
        "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "monosans_http",
        "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "clarketm_proxy",
        "url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "roosterkid_https",
        "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "category": CAT_PROXY,
        "format": "ip_port",
        "reliability": 3,
    },
    {
        "id": "proxifly_all",
        "url": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
        "category": CAT_PROXY,
        "format": "protocol_ip_port",
        "reliability": 3,
    },
    {
        "id": "firehol_socks_proxy",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/socks_proxy.ipset",
        "category": CAT_PROXY,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "firehol_socks_proxy_7d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/socks_proxy_7d.ipset",
        "category": CAT_PROXY,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "firehol_socks_proxy_30d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/socks_proxy_30d.ipset",
        "category": CAT_PROXY,
        "format": "ipset_hash",
        "reliability": 4,
    },
    # --- BOT IPs ---
    {
        "id": "firehol_botvrij_src",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/botvrij_src.ipset",
        "category": CAT_BOT,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "firehol_blocklist_de_bots",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/blocklist_de_bots.ipset",
        "category": CAT_BOT,
        "format": "ipset_hash",
        "reliability": 4,
    },
    {
        "id": "firehol_botscout_30d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/botscout_30d.ipset",
        "category": CAT_BOT,
        "format": "ipset_hash",
        "reliability": 4,
    },
    # --- ABUSE / MALICIOUS IPs ---
    {
        "id": "stamparm_ipsum",
        "url": "https://raw.githubusercontent.com/stamparm/ipsum/master/ipsum.txt",
        "category": CAT_ABUSE,
        "format": "ip_score",
        "reliability": 5,
        # Only include IPs with score >= MIN_IPSUM_SCORE (set at runtime)
    },
    {
        "id": "stamparm_blackbook",
        "url": "https://raw.githubusercontent.com/stamparm/blackbook/master/blackbook.txt",
        "category": CAT_ABUSE,
        "format": "ip_domain",
        "reliability": 4,
    },
    {
        "id": "firehol_level1",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
        "category": CAT_ABUSE,
        "format": "cidr_or_ip",
        "reliability": 5,
    },
    {
        "id": "firehol_level2",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level2.netset",
        "category": CAT_ABUSE,
        "format": "cidr_or_ip",
        "reliability": 4,
    },
    {
        "id": "firehol_abusers_1d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_1d.netset",
        "category": CAT_ABUSE,
        "format": "cidr_or_ip",
        "reliability": 4,
    },
    {
        "id": "firehol_abusers_30d",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_30d.netset",
        "category": CAT_ABUSE,
        "format": "cidr_or_ip",
        "reliability": 4,
    },
    {
        "id": "feodo_tracker",
        "url": "https://feodotracker.abuse.ch/downloads/ipblocklist.txt",
        "category": CAT_ABUSE,
        "format": "plain_ip",
        "reliability": 5,
    },
    {
        "id": "dshield_blocklist",
        "url": "https://feeds.dshield.org/block.txt",
        "category": CAT_ABUSE,
        "format": "dshield",
        "reliability": 5,
    },
    {
        "id": "spamhaus_drop",
        "url": "https://www.spamhaus.org/drop/drop.txt",
        "category": CAT_ABUSE,
        "format": "spamhaus",
        "reliability": 5,
    },
    {
        "id": "spamhaus_edrop",
        "url": "https://www.spamhaus.org/drop/edrop.txt",
        "category": CAT_ABUSE,
        "format": "spamhaus",
        "reliability": 5,
    },
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    return logging.getLogger("consolidate-lists")


log = logging.getLogger("consolidate-lists")

# ---------------------------------------------------------------------------
# Download with caching
# ---------------------------------------------------------------------------

def cache_path(cache_dir: Path, source_id: str) -> Path:
    return cache_dir / f"{source_id}.cache"


def cache_is_fresh(path: Path, ttl_hours: float) -> bool:
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_hours * 3600


def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> Optional[str]:
    """Download URL content as text. Returns None on any error."""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; iGaming-ThreatIntel/1.0; "
                    "+https://github.com/firehol/blocklist-ipsets)"
                )
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Decode, trying UTF-8 first then latin-1 as fallback
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1", errors="replace")
    except HTTPError as exc:
        log.warning("HTTP %d fetching %s", exc.code, url)
        return None
    except URLError as exc:
        log.warning("URL error fetching %s: %s", url, exc.reason)
        return None
    except TimeoutError:
        log.warning("Timeout fetching %s", url)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Unexpected error fetching %s: %s", url, exc)
        return None


def get_source_text(
    source: Dict,
    cache_dir: Path,
    ttl_hours: float,
    no_download: bool,
) -> Optional[str]:
    """Return raw text for a source, using cache when possible."""
    c_path = cache_path(cache_dir, source["id"])

    if cache_is_fresh(c_path, ttl_hours):
        log.debug("Cache hit: %s", source["id"])
        return c_path.read_text(encoding="utf-8", errors="replace")

    if no_download:
        if c_path.exists():
            log.info("Offline mode — using stale cache for %s", source["id"])
            return c_path.read_text(encoding="utf-8", errors="replace")
        log.warning("Offline mode — no cache for %s, skipping", source["id"])
        return None

    log.info("Downloading %s from %s", source["id"], source["url"])
    text = fetch_url(source["url"])
    if text is not None:
        c_path.write_text(text, encoding="utf-8")
        log.debug("Cached %s (%d bytes)", source["id"], len(text))
    return text

# ---------------------------------------------------------------------------
# IP validation helpers
# ---------------------------------------------------------------------------

def is_valid_ip(token: str) -> bool:
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def is_valid_network(token: str) -> bool:
    try:
        ipaddress.ip_network(token, strict=False)
        return True
    except ValueError:
        return False


def normalise_network(token: str) -> str:
    """Return the canonical network address string (host bits zeroed)."""
    net = ipaddress.ip_network(token, strict=False)
    return str(net)


def is_private_or_reserved(addr: str) -> bool:
    """Return True for RFC1918, loopback, link-local, multicast, etc."""
    try:
        obj = ipaddress.ip_address(addr)
        return (
            obj.is_private
            or obj.is_loopback
            or obj.is_link_local
            or obj.is_multicast
            or obj.is_reserved
            or obj.is_unspecified
        )
    except ValueError:
        return True


def strip_port(token: str) -> Optional[str]:
    """Extract IP from IP:PORT strings. Handles IPv6 brackets."""
    # IPv6 with port: [::1]:8080
    m = re.match(r"^\[(.+)\]:(\d+)$", token)
    if m:
        return m.group(1)
    # IPv4 with port: 1.2.3.4:8080
    parts = token.rsplit(":", 1)
    if len(parts) == 2:
        candidate = parts[0]
        if is_valid_ip(candidate):
            return candidate
    # No port
    if is_valid_ip(token):
        return token
    return None

# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------

def parse_plain_ip(text: str) -> Generator[str, None, None]:
    """One IP per line, lines starting with # are comments."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if is_valid_ip(line):
            yield line


def parse_ipset_hash(text: str) -> Generator[str, None, None]:
    """FireHOL ipset format: comments start with #, entries are IPs or CIDRs."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if is_valid_ip(line):
            yield line
        elif is_valid_network(line):
            yield normalise_network(line)


def parse_cidr(text: str) -> Generator[str, None, None]:
    """One CIDR per line, blank/comment lines ignored."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if is_valid_network(line):
            yield normalise_network(line)
        elif is_valid_ip(line):
            yield line


def parse_cidr_or_ip(text: str) -> Generator[str, None, None]:
    """FireHOL .netset format: IPs and CIDRs mixed, # comments."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line:
            if is_valid_network(line):
                yield normalise_network(line)
        else:
            if is_valid_ip(line):
                yield line


def parse_ip_port(text: str) -> Generator[str, None, None]:
    """IP:PORT format — extract IP, discard port."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ip = strip_port(line)
        if ip and is_valid_ip(ip):
            yield ip


def parse_protocol_ip_port(text: str) -> Generator[str, None, None]:
    """protocol://IP:PORT format (e.g., socks5://1.2.3.4:1080)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip protocol prefix
        if "://" in line:
            line = line.split("://", 1)[1]
        ip = strip_port(line)
        if ip and is_valid_ip(ip):
            yield ip


def parse_ip_score(text: str, min_score: int = DEFAULT_MIN_IPSUM_SCORE) -> Generator[str, None, None]:
    """stamparm/ipsum format: IP<TAB>score. Only yield if score >= min_score."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            # Also handle space-separated
            parts = line.split()
        if len(parts) >= 2:
            ip_token = parts[0].strip()
            try:
                score = int(parts[1].strip())
            except ValueError:
                continue
            if score >= min_score and is_valid_ip(ip_token):
                yield ip_token


def parse_ip_domain(text: str) -> Generator[str, None, None]:
    """stamparm/blackbook: IP<TAB>hostname. Extract IP."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 1:
            ip_token = parts[0].strip()
            if is_valid_ip(ip_token):
                yield ip_token


def parse_csv_cidr(text: str) -> Generator[str, None, None]:
    """jhassine/server-ip-addresses CSV: cidr,hostmin,hostmax,vendor"""
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        cidr = row.get("cidr", "").strip()
        if cidr and is_valid_network(cidr):
            yield normalise_network(cidr)


def parse_dshield(text: str) -> Generator[str, None, None]:
    """DShield block.txt: tab-separated Start<TAB>End<TAB>/24 CIDR<TAB>..."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            # Third column is the /24 CIDR range
            cidr = parts[2].strip()
            if is_valid_network(cidr):
                yield normalise_network(cidr)
            elif is_valid_ip(cidr):
                yield cidr


def parse_spamhaus(text: str) -> Generator[str, None, None]:
    """Spamhaus DROP/eDROP: CIDR ; SBL-ref or ; comment lines."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        # Strip inline comment
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        if is_valid_network(line):
            yield normalise_network(line)
        elif is_valid_ip(line):
            yield line


# Map format string to parser function
FORMAT_PARSERS = {
    "plain_ip": parse_plain_ip,
    "ipset_hash": parse_ipset_hash,
    "cidr": parse_cidr,
    "cidr_or_ip": parse_cidr_or_ip,
    "ip_port": parse_ip_port,
    "protocol_ip_port": parse_protocol_ip_port,
    "ip_score": parse_ip_score,
    "ip_domain": parse_ip_domain,
    "csv_cidr": parse_csv_cidr,
    "dshield": parse_dshield,
    "spamhaus": parse_spamhaus,
}

# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

class ConsolidationResult:
    """Holds all processed IP data grouped by category."""

    def __init__(self) -> None:
        # Each set holds canonical string representations (IP or CIDR)
        self.by_category: Dict[str, Set[str]] = {cat: set() for cat in ALL_CATEGORIES}
        # source_id -> count of entries parsed
        self.source_counts: Dict[str, int] = {}
        # source_id -> error message (None = success)
        self.source_errors: Dict[str, Optional[str]] = {}
        # Total raw entries before deduplication
        self.raw_totals: Dict[str, int] = {cat: 0 for cat in ALL_CATEGORIES}

    def add_entries(self, category: str, entries: List[str], source_id: str) -> None:
        before = len(self.by_category[category])
        self.by_category[category].update(entries)
        after = len(self.by_category[category])
        self.raw_totals[category] += len(entries)
        self.source_counts[source_id] = len(entries)
        log.debug(
            "  %s: parsed %d entries, %d new unique",
            source_id, len(entries), after - before,
        )

    @property
    def all_entries(self) -> Set[str]:
        combined: Set[str] = set()
        for s in self.by_category.values():
            combined.update(s)
        return combined

    def total_unique(self) -> int:
        return len(self.all_entries)

    def total_raw(self) -> int:
        return sum(self.raw_totals.values())

    def duplicates_removed(self) -> int:
        return self.total_raw() - self.total_unique()


def process_source(
    source: Dict,
    text: str,
    min_ipsum_score: int,
) -> List[str]:
    """Parse raw text for a source and return list of valid IP/CIDR strings."""
    fmt = source["format"]
    parser = FORMAT_PARSERS.get(fmt)
    if parser is None:
        log.error("Unknown format '%s' for source %s", fmt, source["id"])
        return []

    if fmt == "ip_score":
        entries = list(parse_ip_score(text, min_score=min_ipsum_score))
    else:
        entries = list(parser(text))  # type: ignore[call-arg]

    # Filter out private/reserved addresses (they don't belong in blocklists)
    filtered: List[str] = []
    skipped = 0
    for entry in entries:
        if "/" in entry:
            # CIDR — keep as-is, too expensive to check every address in range
            filtered.append(entry)
        else:
            if not is_private_or_reserved(entry):
                filtered.append(entry)
            else:
                skipped += 1

    if skipped > 0:
        log.debug("  %s: skipped %d private/reserved IPs", source["id"], skipped)

    return filtered


def consolidate(
    sources: List[Dict],
    cache_dir: Path,
    ttl_hours: float,
    no_download: bool,
    min_ipsum_score: int,
) -> ConsolidationResult:
    result = ConsolidationResult()

    for source in sources:
        sid = source["id"]
        try:
            text = get_source_text(source, cache_dir, ttl_hours, no_download)
            if text is None:
                result.source_errors[sid] = "download failed or no cache"
                result.source_counts[sid] = 0
                continue

            entries = process_source(source, text, min_ipsum_score)
            result.add_entries(source["category"], entries, sid)
            result.source_errors[sid] = None

        except Exception as exc:  # noqa: BLE001
            log.error("Error processing source %s: %s", sid, exc, exc_info=True)
            result.source_errors[sid] = str(exc)
            result.source_counts[sid] = 0

    return result

# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_plain_file(path: Path, entries: Set[str], header_lines: List[str]) -> int:
    """Write sorted entries to a plain text file with header comments."""
    sorted_entries = sorted(entries)
    lines = [f"# {h}" for h in header_lines]
    lines.append(f"# Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"# Total entries: {len(sorted_entries)}")
    lines.append("")
    lines.extend(sorted_entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(sorted_entries)


def write_consolidated(path: Path, result: ConsolidationResult) -> int:
    """Write all entries with category annotation."""
    lines: List[str] = [
        "# Consolidated IP Threat Intelligence",
        "# Format: <ip_or_cidr> <category>",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        f"# Total unique entries: {result.total_unique()}",
        "",
    ]
    count = 0
    for category in ALL_CATEGORIES:
        entries = result.by_category[category]
        for entry in sorted(entries):
            lines.append(f"{entry} {category}")
            count += 1

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def write_redis_script(path: Path, result: ConsolidationResult) -> None:
    """
    Write a Redis shell script using redis-cli pipe mode for bulk import.
    Each category gets its own Redis SET key:
      igaming:threats:tor      -> SADD (set of IPs)
      igaming:threats:vpn      -> SADD (set of CIDRs)
      igaming:threats:proxy    -> SADD
      igaming:threats:datacenter -> SADD
      igaming:threats:bot      -> SADD
      igaming:threats:abuse    -> SADD
      igaming:threats:all      -> SADD (combined)

    Also writes an IP -> category hash:
      igaming:ip:<ip>  -> category string (for O(1) lookup)
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    lines: List[str] = [
        "#!/usr/bin/env bash",
        "# Redis bulk load script for iGaming IP threat intelligence",
        f"# Generated: {timestamp}",
        "#",
        "# Usage:",
        "#   bash redis-load.sh                    (uses default redis-cli)",
        "#   REDIS_CLI='redis-cli -h 127.0.0.1 -p 6379 -a password' bash redis-load.sh",
        "#   cat redis-load.sh | redis-cli --pipe  (pipe mode, faster for large sets)",
        "#",
        "# The script deletes existing keys before loading to ensure clean state.",
        "",
        'REDIS_CLI="${REDIS_CLI:-redis-cli}"',
        "",
    ]

    for category in ALL_CATEGORIES:
        key = f"igaming:threats:{category}"
        entries = sorted(result.by_category[category])
        if not entries:
            continue
        lines.append(f"echo 'Loading {len(entries)} {category} entries...'")
        lines.append(f'$REDIS_CLI DEL "{key}"')
        # Batch into chunks of 1000 to avoid too-long command lines
        for i in range(0, len(entries), 1000):
            chunk = entries[i : i + 1000]
            quoted = " ".join(f'"{e}"' for e in chunk)
            lines.append(f'$REDIS_CLI SADD "{key}" {quoted}')
        lines.append("")

    # Combined all key
    all_entries = sorted(result.all_entries)
    all_key = "igaming:threats:all"
    lines.append(f"echo 'Loading {len(all_entries)} combined entries...'")
    lines.append(f'$REDIS_CLI DEL "{all_key}"')
    for i in range(0, len(all_entries), 1000):
        chunk = all_entries[i : i + 1000]
        quoted = " ".join(f'"{e}"' for e in chunk)
        lines.append(f'$REDIS_CLI SADD "{all_key}" {quoted}')
    lines.append("")

    # Per-IP lookup hash (only for single IPs, not CIDRs)
    lines.append("# Per-IP category lookup hash (igaming:ip:<ip> -> category)")
    lines.append("# Only includes single IPs, not CIDR ranges.")
    for category in ALL_CATEGORIES:
        ip_only = [e for e in result.by_category[category] if "/" not in e]
        if not ip_only:
            continue
        for i in range(0, len(ip_only), 500):
            chunk = ip_only[i : i + 500]
            # MSET for hash: igaming:ip:<ip> category
            mset_args = " ".join(f'"igaming:ip:{ip}" "{category}"' for ip in chunk)
            lines.append(f"$REDIS_CLI MSET {mset_args}")

    lines.append("")
    lines.append("echo 'Done.'")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def write_cloudflare_kv(path: Path, result: ConsolidationResult) -> None:
    """
    Write Cloudflare Workers KV bulk upload JSON.
    Format: array of {key: string, value: string} objects.
    Key: ip or cidr string
    Value: JSON object with category and threat info

    Compatible with:
      wrangler kv:bulk put --binding THREAT_IPS threat-intel-kv.json
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    kv_entries = []

    # Build a map from entry -> list of categories (an IP might appear in multiple)
    entry_categories: Dict[str, List[str]] = defaultdict(list)
    for category in ALL_CATEGORIES:
        for entry in result.by_category[category]:
            entry_categories[entry].append(category)

    for entry, categories in entry_categories.items():
        primary_cat = categories[0]
        value = json.dumps(
            {
                "categories": categories,
                "primary": primary_cat,
                "is_cidr": "/" in entry,
                "updated": timestamp,
            },
            separators=(",", ":"),
        )
        kv_entries.append({"key": entry, "value": value})

    # Cloudflare KV bulk upload supports up to 10,000 per request.
    # Write multiple files if needed.
    total = len(kv_entries)
    if total <= CF_KV_BATCH_SIZE:
        path.write_text(json.dumps(kv_entries, indent=2), encoding="utf-8")
        log.info("Cloudflare KV: %d entries written to %s", total, path.name)
    else:
        # Write numbered batch files
        base = path.stem
        suffix = path.suffix
        parent = path.parent
        for batch_num, start in enumerate(range(0, total, CF_KV_BATCH_SIZE), 1):
            chunk = kv_entries[start : start + CF_KV_BATCH_SIZE]
            batch_path = parent / f"{base}-batch{batch_num:03d}{suffix}"
            batch_path.write_text(json.dumps(chunk, indent=2), encoding="utf-8")
            log.info(
                "Cloudflare KV batch %d: %d entries written to %s",
                batch_num, len(chunk), batch_path.name,
            )
        # Write an index file at the main path
        index = {
            "total_entries": total,
            "batches": (total + CF_KV_BATCH_SIZE - 1) // CF_KV_BATCH_SIZE,
            "batch_size": CF_KV_BATCH_SIZE,
            "generated": timestamp,
            "import_command": (
                f"for f in {base}-batch*.json; do "
                "wrangler kv:bulk put --binding THREAT_IPS \"$f\"; done"
            ),
        }
        path.write_text(json.dumps(index, indent=2), encoding="utf-8")


def write_aws_waf_ipset(path: Path, result: ConsolidationResult) -> None:
    """
    Write AWS WAF IP set JSON files.
    AWS WAF accepts IPv4/IPv6 CIDR notation.
    Single IPs are converted to /32 (IPv4) or /128 (IPv6).
    AWS WAF hard limit: 10,000 addresses per IP set.

    One file per category + one combined file (truncated at limit).
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    parent = path.parent
    base = path.stem

    def to_aws_cidr(entry: str) -> Optional[str]:
        """Convert IP or CIDR to AWS WAF format."""
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
                return str(net)
            except ValueError:
                return None
        else:
            try:
                addr = ipaddress.ip_address(entry)
                if isinstance(addr, ipaddress.IPv4Address):
                    return f"{addr}/32"
                else:
                    return f"{addr}/128"
            except ValueError:
                return None

    # Per-category files
    for category in ALL_CATEGORIES:
        entries = result.by_category[category]
        if not entries:
            continue

        cidrs = []
        for e in sorted(entries):
            c = to_aws_cidr(e)
            if c:
                cidrs.append(c)

        # Separate IPv4 and IPv6 (AWS WAF IP sets are type-specific)
        ipv4_cidrs = [c for c in cidrs if ":" not in c]
        ipv6_cidrs = [c for c in cidrs if ":" in c]

        for ip_version, addr_list in [("ipv4", ipv4_cidrs), ("ipv6", ipv6_cidrs)]:
            if not addr_list:
                continue
            truncated = addr_list[:AWS_WAF_MAX_ADDRESSES]
            was_truncated = len(addr_list) > AWS_WAF_MAX_ADDRESSES
            cat_path = parent / f"aws-waf-{category}-{ip_version}.json"
            payload = {
                "Name": f"igaming-threats-{category}-{ip_version}",
                "Description": (
                    f"iGaming IP threat intelligence: {category} ({ip_version}). "
                    f"Generated {timestamp}."
                    + (
                        f" TRUNCATED: {len(addr_list)} total, showing first {AWS_WAF_MAX_ADDRESSES}."
                        if was_truncated
                        else ""
                    )
                ),
                "Scope": "REGIONAL",
                "IPAddressVersion": "IPV4" if ip_version == "ipv4" else "IPV6",
                "Addresses": truncated,
                "_metadata": {
                    "total_available": len(addr_list),
                    "total_included": len(truncated),
                    "truncated": was_truncated,
                    "generated": timestamp,
                },
            }
            cat_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Combined file (all categories, IPv4 only to keep it usable)
    all_ipv4 = []
    seen: Set[str] = set()
    for category in ALL_CATEGORIES:
        for e in result.by_category[category]:
            c = to_aws_cidr(e)
            if c and ":" not in c and c not in seen:
                all_ipv4.append(c)
                seen.add(c)

    all_ipv4.sort()
    truncated_all = all_ipv4[:AWS_WAF_MAX_ADDRESSES]
    all_path = parent / f"{base}.json"
    payload_all = {
        "Name": "igaming-threats-all-ipv4",
        "Description": (
            f"iGaming IP threat intelligence: all categories combined (IPv4 only). "
            f"Generated {timestamp}."
            + (
                f" TRUNCATED: {len(all_ipv4)} total, showing first {AWS_WAF_MAX_ADDRESSES}."
                if len(all_ipv4) > AWS_WAF_MAX_ADDRESSES
                else ""
            )
        ),
        "Scope": "REGIONAL",
        "IPAddressVersion": "IPV4",
        "Addresses": truncated_all,
        "_metadata": {
            "total_available": len(all_ipv4),
            "total_included": len(truncated_all),
            "truncated": len(all_ipv4) > AWS_WAF_MAX_ADDRESSES,
            "generated": timestamp,
            "per_category_files": [
                f"aws-waf-{cat}-ipv4.json"
                for cat in ALL_CATEGORIES
                if result.by_category[cat]
            ],
        },
    }
    all_path.write_text(json.dumps(payload_all, indent=2), encoding="utf-8")


def write_stats(path: Path, result: ConsolidationResult, elapsed: float) -> None:
    """Write a JSON stats file summarising the run."""
    timestamp = datetime.now(timezone.utc).isoformat()

    category_stats = {}
    for cat in ALL_CATEGORIES:
        entries = result.by_category[cat]
        ip_count = sum(1 for e in entries if "/" not in e)
        cidr_count = sum(1 for e in entries if "/" in e)
        category_stats[cat] = {
            "unique_entries": len(entries),
            "ip_count": ip_count,
            "cidr_count": cidr_count,
            "raw_total": result.raw_totals[cat],
        }

    stats = {
        "generated": timestamp,
        "elapsed_seconds": round(elapsed, 2),
        "total_unique_entries": result.total_unique(),
        "total_raw_entries": result.total_raw(),
        "duplicates_removed": result.duplicates_removed(),
        "categories": category_stats,
        "sources": [
            {
                "id": sid,
                "entries_parsed": result.source_counts.get(sid, 0),
                "error": result.source_errors.get(sid),
            }
            for sid in sorted(result.source_counts.keys() | result.source_errors.keys())
        ],
    }

    path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consolidate IP threat intelligence lists for iGaming fraud detection."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "output",
        help="Directory to write output files (default: ./output)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).parent / "cache",
        help="Directory for cached raw downloads (default: ./cache)",
    )
    parser.add_argument(
        "--cache-ttl-hours",
        type=float,
        default=DEFAULT_CACHE_TTL_HOURS,
        help=f"Cache TTL in hours (default: {DEFAULT_CACHE_TTL_HOURS})",
    )
    parser.add_argument(
        "--min-ipsum-score",
        type=int,
        default=DEFAULT_MIN_IPSUM_SCORE,
        help=(
            f"Minimum IPsum blacklist score to include (default: {DEFAULT_MIN_IPSUM_SCORE}). "
            "Higher = fewer false positives. Range 1-11."
        ),
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Offline mode: use cached data only, do not fetch from network.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"consolidate-lists {SCRIPT_VERSION}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    start_time = time.time()

    log.info("=" * 60)
    log.info("iGaming IP Threat Intelligence Consolidation v%s", SCRIPT_VERSION)
    log.info("=" * 60)
    log.info("Output dir : %s", args.output_dir)
    log.info("Cache dir  : %s", args.cache_dir)
    log.info("Cache TTL  : %.1f hours", args.cache_ttl_hours)
    log.info("IPsum score: >= %d", args.min_ipsum_score)
    log.info("Sources    : %d configured", len(SOURCES))
    if args.no_download:
        log.info("Mode       : OFFLINE (using cached data only)")

    # Ensure directories exist
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    # --- Download and process all sources ---
    log.info("")
    log.info("Phase 1: Downloading and parsing sources")
    log.info("-" * 40)
    result = consolidate(
        SOURCES,
        args.cache_dir,
        args.cache_ttl_hours,
        args.no_download,
        args.min_ipsum_score,
    )

    # --- Write output files ---
    log.info("")
    log.info("Phase 2: Writing output files")
    log.info("-" * 40)

    out = args.output_dir

    # Category-specific plain files
    write_plain_file(
        out / "tor-exits.txt",
        result.by_category[CAT_TOR],
        [
            "Tor Exit Node IP Addresses",
            "Sources: Tor Project, FireHOL, Emerging Threats, dan.me.uk, SecOps-Institute",
            "Use: Block or flag connections from Tor exit nodes",
        ],
    )
    log.info("  tor-exits.txt        : %d entries", len(result.by_category[CAT_TOR]))

    write_plain_file(
        out / "vpn-ips.txt",
        result.by_category[CAT_VPN],
        [
            "VPN Provider IP Ranges (CIDR)",
            "Sources: X4BNet/lists_vpn",
            "Use: Detect connections from commercial VPN providers",
        ],
    )
    log.info("  vpn-ips.txt          : %d entries", len(result.by_category[CAT_VPN]))

    write_plain_file(
        out / "proxy-ips.txt",
        result.by_category[CAT_PROXY],
        [
            "Open Proxy IP Addresses",
            "Sources: mmpx12, TheSpeedX, ShiftyTR, hookzof, monosans, clarketm, roosterkid, proxifly, FireHOL",
            "Use: Detect HTTP/SOCKS proxy usage",
        ],
    )
    log.info("  proxy-ips.txt        : %d entries", len(result.by_category[CAT_PROXY]))

    write_plain_file(
        out / "datacenter-ranges.txt",
        result.by_category[CAT_DATACENTER],
        [
            "Datacenter and Hosting Provider IP Ranges (CIDR)",
            "Sources: X4BNet datacenter list, jhassine/server-ip-addresses",
            "Use: Detect bot traffic and scripted players from cloud/hosting IPs",
        ],
    )
    log.info("  datacenter-ranges.txt: %d entries", len(result.by_category[CAT_DATACENTER]))

    write_plain_file(
        out / "bot-ips.txt",
        result.by_category[CAT_BOT],
        [
            "Known Bot and Automated Abuse IP Addresses",
            "Sources: FireHOL (botvrij, blocklist.de, botscout)",
            "Use: Detect automated registration, credential stuffing, scripted gameplay",
        ],
    )
    log.info("  bot-ips.txt          : %d entries", len(result.by_category[CAT_BOT]))

    write_plain_file(
        out / "abuse-ips.txt",
        result.by_category[CAT_ABUSE],
        [
            "Malicious/Abusive IP Addresses and Ranges",
            "Sources: IPsum, FireHOL Level1/2, Feodo Tracker, DShield, Spamhaus DROP/eDROP, blackbook",
            "Use: Block or risk-score connections from known malicious infrastructure",
        ],
    )
    log.info("  abuse-ips.txt        : %d entries", len(result.by_category[CAT_ABUSE]))

    # Consolidated file
    total_consolidated = write_consolidated(
        out / "consolidated-threats.txt",
        result,
    )
    log.info("  consolidated-threats.txt: %d entries", total_consolidated)

    # Redis bulk load script
    write_redis_script(out / "redis-load.sh", result)
    log.info("  redis-load.sh        : written")

    # Cloudflare KV bulk upload
    write_cloudflare_kv(out / "cloudflare-kv-bulk.json", result)
    log.info("  cloudflare-kv-bulk.json: written")

    # AWS WAF IP set
    write_aws_waf_ipset(out / "aws-waf-ipset.json", result)
    log.info("  aws-waf-ipset.json   : written (+ per-category files)")

    # Stats file
    elapsed = time.time() - start_time
    write_stats(out / "run-stats.json", result, elapsed)
    log.info("  run-stats.json       : written")

    # --- Summary ---
    log.info("")
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("Elapsed          : %.1f seconds", elapsed)
    log.info("Raw entries      : %d", result.total_raw())
    log.info("Unique entries   : %d", result.total_unique())
    log.info("Duplicates removed: %d", result.duplicates_removed())
    log.info("")
    log.info("By category:")
    for cat in ALL_CATEGORIES:
        entries = result.by_category[cat]
        log.info("  %-15s: %d", cat, len(entries))

    # Report failed sources
    failed = {sid: err for sid, err in result.source_errors.items() if err is not None}
    if failed:
        log.info("")
        log.warning("Failed sources (%d):", len(failed))
        for sid, err in sorted(failed.items()):
            log.warning("  %-35s: %s", sid, err)
    else:
        log.info("")
        log.info("All sources downloaded successfully.")

    log.info("")
    log.info("Output written to: %s", args.output_dir)
    log.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
