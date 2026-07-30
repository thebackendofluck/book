#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
iprep-update.py — Convert threat intelligence feeds to Suricata iprep format.

Manages download, validation, conversion, and atomic deployment of IP reputation
data for Suricata. Designed for iGaming platform threat intelligence workflows.

Usage:
    iprep-update.py [--dry-run] [--force] [--loglevel DEBUG|INFO|WARNING]

Environment:
    IPREP_DIR           — Output directory for reputation files (default: /etc/suricata/iprep)
    IPREP_STAGING_DIR   — Staging directory for downloads and validation (default: /var/lib/iprep)
    BLOCKLIST_SERVE_DIR — Directory of plain lists served to OPNsense (default: /var/lib/iprep/serve)
    SURICATA_SOCKET     — Path to Suricata's Unix socket for reload signaling
    ABUSEIPDB_API_KEY   — Enables the AbuseIPDB bulk blacklist feed (category 12)

Install note: iprep_abuseipdb.py must sit in the same directory as this file.
The AbuseIPDB feed is skipped with a warning if the import fails.
"""

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, TypedDict
from urllib.error import URLError
from urllib.request import Request, urlopen

# Sibling modules are imported by name, so make the script's own directory
# importable no matter where it is invoked from or symlinked to.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IPREP_DIR = Path(os.environ.get("IPREP_DIR", "/etc/suricata/iprep"))
STAGING_DIR = Path(os.environ.get("IPREP_STAGING_DIR", "/var/lib/iprep"))
SERVE_DIR = Path(os.environ.get("BLOCKLIST_SERVE_DIR", str(STAGING_DIR / "serve")))
SURICATA_SOCKET = os.environ.get("SURICATA_SOCKET", "/var/run/suricata/suricata-command.socket")

# Output reputation file — Suricata reads this
REPUTATION_FILE = IPREP_DIR / "reputation.list"
REPUTATION_FILE_BACKUP = IPREP_DIR / "reputation.list.bak"
REPUTATION_METADATA = IPREP_DIR / "reputation.meta.json"

# Category 15 lives in its own file so the pass rule (sid 9100090) has data.
# Both files must be listed under reputation-files: in suricata.yaml.
WHITELIST_REPUTATION_FILE = IPREP_DIR / "whitelist.list"

# Staging paths
DOWNLOAD_DIR = STAGING_DIR / "downloads"
PROCESSED_DIR = STAGING_DIR / "processed"

# Category IDs must match /etc/suricata/iprep/categories.txt.
# Every category referenced by ip-reputation.rules needs a feed below, and
# every feed needs a rule, otherwise one side of the pipeline is dead weight.
CATEGORY_BOTNET = 1
CATEGORY_SCANNER = 2
CATEGORY_PROXYVPN = 5
CATEGORY_TOREXIT = 6
CATEGORY_CREDSTUFFING = 7
CATEGORY_DDOS = 8  # reserved, deliberately unpopulated — see categories.txt
CATEGORY_DATASHIELD = 10
CATEGORY_EMERGING_THREATS = 11
CATEGORY_ABUSEIPDB = 12
CATEGORY_SPAMHAUS = 13
CATEGORY_FIREHOL = 14
CATEGORY_WHITELIST = 15

# Score assignments per source (1-127; higher = higher confidence).
# These reflect the relative reliability of each feed based on our empirical
# testing. Changing one means re-checking the thresholds in
# ip-reputation.rules: a score below a rule's threshold silences that rule.
SOURCE_SCORE = {
    "datashield_recommended": 75,
    # 45 keeps critical-list-only addresses inside the 21-50 band that rule
    # 9100003 alerts on. At the old 60 that band was unreachable and the rule
    # could never fire.
    "datashield_aggressive": 45,
    "emerging_threats": 80,
    "spamhaus_drop": 90,
    "spamhaus_edrop": 85,
    "firehol_level1": 85,
    "firehol_level2": 65,
    "abuseipdb": 70,
    "tor_exit_nodes": 60,
    "blocklist_de_bruteforce": 70,
    "blocklist_de_bots": 70,
    "firehol_dshield": 75,
    "firehol_sslproxies": 65,
    "firehol_socks_proxy": 65,
    "feodo_c2": 95,
    "whitelist": 100,
}

# Minimum IPv4 prefix length accepted from a feed. A single malformed line
# such as 0.0.0.0/0 or 10.0.0.0/8 would otherwise blackhole the internet, and
# the entry count checks would not notice because the line count looks fine.
DEFAULT_MIN_PREFIXLEN = 24
# Floor that no per-feed override may go below, whatever the config says.
HARD_MIN_PREFIXLEN = 12
# Default cap on the total address space one feed may contribute. Catches the
# corruption case where every line is individually plausible.
DEFAULT_MAX_ADDRESSES = 4_000_000

class FeedConfig(TypedDict, total=False):
    name: str
    url: str
    category: int
    score: int
    comment_char: str
    min_entries: int
    max_entries: int
    # Broadest prefix accepted from this feed (default DEFAULT_MIN_PREFIXLEN).
    # Raise the breadth only for feeds that publish aggregates on purpose.
    min_prefixlen: int
    # Cap on summed address space for this feed (default DEFAULT_MAX_ADDRESSES).
    max_addresses: int
    # True for feeds that are legitimately empty sometimes (live C2 lists).
    allow_empty: bool
    # Non-HTTP-GET fetchers, currently only "abuseipdb".
    downloader: str
    enabled: bool


# Feed definitions.
#
# min_prefixlen and max_addresses are set from the observed shape of each
# feed, measured 2026-07-26. Data-Shield and the per-address lists publish
# only /32s. Spamhaus DROP and the Emerging Threats block list (which is
# largely DROP re-published) go down to /12 by design, and clamping them to
# /24 would discard roughly half their entries. FireHOL level 1 carries a
# handful of reserved-space aggregates (0.0.0.0/8, 224.0.0.0/3 and similar)
# that the /12 floor drops; that costs five entries out of ~4600 and reserved
# space belongs in firewall bogon filtering rather than in reputation data.
FEEDS: List[FeedConfig] = [
    {
        "name": "datashield_recommended",
        "url": "https://raw.githubusercontent.com/duggytuxy/Data-Shield_IPv4_Blocklist/main/prod_data-shield_ipv4_blocklist.txt",
        "category": CATEGORY_DATASHIELD,
        "score": SOURCE_SCORE["datashield_recommended"],
        "comment_char": "#",
        "min_entries": 10000,   # Anomaly detection: alert if fewer entries than expected
        "max_entries": 500000,  # Anomaly detection: alert if suspiciously many entries
        "min_prefixlen": 24,
        "max_addresses": 1_000_000,
        "enabled": True,
    },
    {
        "name": "datashield_aggressive",
        "url": "https://raw.githubusercontent.com/duggytuxy/Data-Shield_IPv4_Blocklist/main/prod_critical_data-shield_ipv4_blocklist.txt",
        "category": CATEGORY_DATASHIELD,
        "score": SOURCE_SCORE["datashield_aggressive"],
        "comment_char": "#",
        "min_entries": 5000,
        "max_entries": 1000000,
        "min_prefixlen": 24,
        "max_addresses": 1_000_000,
        "enabled": True,
    },
    {
        "name": "emerging_threats",
        "url": "https://rules.emergingthreats.net/fwrules/emerging-Block-IPs.txt",
        "category": CATEGORY_EMERGING_THREATS,
        "score": SOURCE_SCORE["emerging_threats"],
        "comment_char": "#",
        "min_entries": 500,
        "max_entries": 200000,
        "min_prefixlen": 12,
        "max_addresses": 32_000_000,
        "enabled": True,
    },
    {
        "name": "spamhaus_drop",
        "url": "https://www.spamhaus.org/drop/drop.txt",
        "category": CATEGORY_SPAMHAUS,
        "score": SOURCE_SCORE["spamhaus_drop"],
        "comment_char": ";",
        "min_entries": 100,
        "max_entries": 50000,
        "min_prefixlen": 12,
        "max_addresses": 32_000_000,
        "enabled": True,
    },
    {
        "name": "spamhaus_edrop",
        "url": "https://www.spamhaus.org/drop/edrop.txt",
        "category": CATEGORY_SPAMHAUS,
        "score": SOURCE_SCORE["spamhaus_edrop"],
        "comment_char": ";",
        "min_entries": 50,
        "max_entries": 50000,
        "enabled": False,  # eDROP merged into DROP as of 2026 — no separate entries
    },
    {
        "name": "firehol_level1",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
        "category": CATEGORY_FIREHOL,
        "score": SOURCE_SCORE["firehol_level1"],
        "comment_char": "#",
        "min_entries": 1000,
        "max_entries": 500000,
        "min_prefixlen": 12,
        "max_addresses": 40_000_000,
        "enabled": True,
    },
    # --- Feeds below exist so that the categories their rules reference are
    # --- actually written. Before they were added, 11 of the 15 rules in
    # --- ip-reputation.rules referenced categories nothing populated.
    {
        # Category 6 (TorExit), rules 9100050 and 9100051. The Tor Project's
        # own bulk exit list, so this is authoritative rather than inferred.
        "name": "tor_exit_nodes",
        "url": "https://check.torproject.org/torbulkexitlist",
        "category": CATEGORY_TOREXIT,
        "score": SOURCE_SCORE["tor_exit_nodes"],
        "comment_char": "#",
        "min_entries": 200,
        "max_entries": 20000,
        "min_prefixlen": 24,
        "max_addresses": 100_000,
        "enabled": True,
    },
    {
        # Category 7 (CredStuffing), rules 9100010-9100012. blocklist.de's
        # bruteforcelogin list: addresses reported for brute-force attempts
        # against web login forms, which is the closest public proxy for
        # credential stuffing infrastructure.
        "name": "blocklist_de_bruteforce",
        "url": "https://lists.blocklist.de/lists/bruteforcelogin.txt",
        "category": CATEGORY_CREDSTUFFING,
        "score": SOURCE_SCORE["blocklist_de_bruteforce"],
        "comment_char": "#",
        "min_entries": 100,
        "max_entries": 200000,
        "min_prefixlen": 24,
        "max_addresses": 100_000,
        "enabled": True,
    },
    {
        # Category 2 (Scanner), rule 9100060.
        "name": "blocklist_de_bots",
        "url": "https://lists.blocklist.de/lists/bots.txt",
        "category": CATEGORY_SCANNER,
        "score": SOURCE_SCORE["blocklist_de_bots"],
        "comment_char": "#",
        "min_entries": 500,
        "max_entries": 500000,
        "min_prefixlen": 24,
        "max_addresses": 500_000,
        "enabled": True,
    },
    {
        # Category 2 (Scanner) as well: the DShield top attacking subnets.
        # Small by design, a couple of dozen /24s.
        "name": "firehol_dshield",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/dshield.netset",
        "category": CATEGORY_SCANNER,
        "score": SOURCE_SCORE["firehol_dshield"],
        "comment_char": "#",
        "min_entries": 5,
        "max_entries": 5000,
        "min_prefixlen": 20,
        "max_addresses": 100_000,
        "enabled": True,
    },
    {
        # Category 5 (ProxyVPN), rules 9100030-9100032. Open and commercial
        # proxies. No public feed enumerates consumer VPN egress reliably, so
        # coverage of the "VPN" half of that category name is partial.
        "name": "firehol_sslproxies",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/sslproxies.ipset",
        "category": CATEGORY_PROXYVPN,
        "score": SOURCE_SCORE["firehol_sslproxies"],
        "comment_char": "#",
        "min_entries": 10,
        "max_entries": 100000,
        "min_prefixlen": 24,
        "max_addresses": 100_000,
        "enabled": True,
    },
    {
        # Category 5 (ProxyVPN) as well.
        "name": "firehol_socks_proxy",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/socks_proxy.ipset",
        "category": CATEGORY_PROXYVPN,
        "score": SOURCE_SCORE["firehol_socks_proxy"],
        "comment_char": "#",
        "min_entries": 10,
        "max_entries": 100000,
        "min_prefixlen": 24,
        "max_addresses": 100_000,
        "enabled": True,
    },
    {
        # Category 1 (Botnet), rule 9100020. abuse.ch Feodo Tracker lists only
        # C2 servers currently observed live, so single digits are normal and
        # an empty file is possible: allow_empty stops that being logged as a
        # feed failure. Replace or supplement with a commercial C2 feed if you
        # have one; the category and the rule stay as they are.
        "name": "feodo_c2",
        "url": "https://feodotracker.abuse.ch/downloads/ipblocklist.txt",
        "category": CATEGORY_BOTNET,
        "score": SOURCE_SCORE["feodo_c2"],
        "comment_char": "#",
        "min_entries": 0,
        "max_entries": 50000,
        "min_prefixlen": 24,
        "max_addresses": 100_000,
        "allow_empty": True,
        "enabled": True,
    },
    {
        # Category 12 (AbuseIPDB), rule 9100080. Fetched through
        # iprep_abuseipdb.download_blacklist because the endpoint needs an API
        # key header; enabled only when ABUSEIPDB_API_KEY is set, so the rule
        # is inert on deployments without a key.
        "name": "abuseipdb_blacklist",
        "url": "https://api.abuseipdb.com/api/v2/blacklist",
        "category": CATEGORY_ABUSEIPDB,
        "score": SOURCE_SCORE["abuseipdb"],
        "comment_char": "#",
        "min_entries": 100,
        "max_entries": 500000,
        "min_prefixlen": 24,
        "max_addresses": 500_000,
        "downloader": "abuseipdb",
        "enabled": bool(os.environ.get("ABUSEIPDB_API_KEY", "")),
    },
]

# Whitelist — these IPs/CIDRs are NEVER written to the blocklist.
# Populate with payment providers, CDN egress, monitoring services, office IPs.
# Format: list of strings parseable by ipaddress.ip_network()
WHITELIST_PATH = IPREP_DIR / "whitelist.txt"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class ReputationEntry(NamedTuple):
    ip: ipaddress.IPv4Network
    category: int
    score: int
    source: str


class FeedResult(NamedTuple):
    feed_name: str
    entries: List[ReputationEntry]
    entry_count: int
    download_ok: bool
    validation_ok: bool
    error: Optional[str]


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: str) -> logging.Logger:
    log = logging.getLogger("iprep-update")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    ))
    log.addHandler(handler)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    return log


log = logging.getLogger("iprep-update")


# ---------------------------------------------------------------------------
# Whitelist management
# ---------------------------------------------------------------------------

def load_whitelist(path: Path) -> Set[ipaddress.IPv4Network]:
    """Load whitelist from file. Returns set of IPv4Network objects."""
    if not path.exists():
        log.warning(f"Whitelist file not found at {path} — using empty whitelist")
        return set()

    whitelist = set()
    with open(path, 'r') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                if '/' not in line:
                    line = line + '/32'
                net = ipaddress.ip_network(line, strict=False)
                whitelist.add(net)
            except ValueError as e:
                log.warning(f"Whitelist line {lineno}: cannot parse '{line}': {e}")

    log.info(f"Loaded {len(whitelist)} whitelist entries from {path}")
    return whitelist


def is_whitelisted(ip: ipaddress.IPv4Network, whitelist: Set[ipaddress.IPv4Network]) -> bool:
    """Check if a network overlaps with any whitelist entry."""
    for wl_net in whitelist:
        if ip.overlaps(wl_net):
            return True
    return False


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_feed(feed: FeedConfig, dest_dir: Path, timeout: int = 90) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Download a feed URL to dest_dir/<feed_name>.txt.
    Returns (success, filepath, error_message).

    Feeds carrying a "downloader" key need more than a plain GET (an API key
    header, pagination) and are delegated to their own module.
    """
    dest_path = dest_dir / f"{feed['name']}.txt"
    url = feed['url']

    if feed.get('downloader') == 'abuseipdb':
        try:
            import iprep_abuseipdb
        except ImportError as e:
            return False, None, (
                f"AbuseIPDB feed enabled but iprep_abuseipdb.py is not importable "
                f"(install it beside this script): {e}"
            )
        ok, err = iprep_abuseipdb.download_blacklist(dest_path)
        return (True, str(dest_path), None) if ok else (False, None, err)

    log.info(f"Downloading {feed['name']} from {url}")

    req = Request(url, headers={
        "User-Agent": "iGaming-Security-Feed/2.0 (threat-intelligence-update)",
        "Accept": "text/plain",
    })

    try:
        start_time = time.monotonic()
        with urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return False, None, f"HTTP {response.status} for {url}"

            content = response.read()

        elapsed = time.monotonic() - start_time
        log.info(f"Downloaded {feed['name']}: {len(content):,} bytes in {elapsed:.1f}s")

        # Write to staging
        with open(dest_path, 'wb') as f:
            f.write(content)

        return True, str(dest_path), None

    except URLError as e:
        return False, None, f"Download failed for {url}: {e}"
    except Exception as e:
        return False, None, f"Unexpected error downloading {url}: {e}"


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

def parse_feed_file(filepath: str, feed: FeedConfig, whitelist: Set[ipaddress.IPv4Network]) -> Tuple[List[ReputationEntry], int, Optional[str]]:
    """
    Parse a downloaded feed file into ReputationEntry objects.
    Returns (entries, raw_line_count, error_message).

    Applies whitelist filtering and the prefix-breadth floor during parse.
    """
    entries = []
    raw_count = 0
    parse_errors = 0
    whitelist_filtered = 0
    too_broad = 0
    comment_char = feed.get('comment_char', '#')

    # A per-feed override may narrow the floor but never widen it past the
    # hard limit, so no config edit can reintroduce a /0 or /8 entry.
    min_prefixlen = max(
        feed.get('min_prefixlen', DEFAULT_MIN_PREFIXLEN),
        HARD_MIN_PREFIXLEN,
    )

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith(comment_char):
                continue

            # Some formats include inline comments after the CIDR (e.g., Spamhaus)
            # Strip trailing comment content
            if ';' in line:
                line = line[:line.index(';')].strip()
            if '#' in line and comment_char != '#':
                line = line[:line.index('#')].strip()
            if not line:
                continue

            raw_count += 1

            try:
                # Normalize to CIDR notation
                if '/' not in line:
                    line = line + '/32'

                net = ipaddress.ip_network(line, strict=False)

                # Skip IPv6 (we only handle IPv4 here)
                if isinstance(net, ipaddress.IPv6Network):
                    continue

                # Reject entries broader than the floor. One bad upstream line
                # ("0.0.0.0/0", a stray "10.0.0.0/8") would otherwise put the
                # whole internet, or a whole cloud region, behind a drop rule.
                if net.prefixlen < min_prefixlen:
                    too_broad += 1
                    if too_broad <= 5:
                        log.warning(
                            f"{feed['name']} line {lineno}: rejecting {net} — "
                            f"broader than /{min_prefixlen} floor"
                        )
                    continue

                # Skip if whitelisted
                if is_whitelisted(net, whitelist):
                    whitelist_filtered += 1
                    continue

                entries.append(ReputationEntry(
                    ip=net,
                    category=feed['category'],
                    score=feed['score'],
                    source=feed['name'],
                ))

            except ValueError:
                parse_errors += 1
                if parse_errors <= 3:
                    log.debug(f"Parse error in {feed['name']} line {lineno}: '{line}'")

    if parse_errors > 0:
        log.warning(f"{feed['name']}: {parse_errors} parse errors out of {raw_count} lines")

    if whitelist_filtered > 0:
        log.info(f"{feed['name']}: {whitelist_filtered} entries suppressed by whitelist")

    if too_broad > 0:
        log.warning(
            f"{feed['name']}: {too_broad} entries rejected as broader than "
            f"/{min_prefixlen}"
        )

    log.info(f"{feed['name']}: parsed {len(entries)} valid entries from {raw_count} data lines")
    return entries, raw_count, None


def validate_feed(entries: List[ReputationEntry], feed: FeedConfig, raw_count: int) -> Tuple[bool, Optional[str]]:
    """
    Validate parsed feed entries against expected ranges.
    Returns (is_valid, error_message).

    Catches: empty downloads, truncated files, HTTP errors served as HTML,
    and feeds whose entry count looks normal while the address space they
    cover has exploded.
    """
    min_entries = feed.get('min_entries', 100)
    max_entries = feed.get('max_entries', 10_000_000)
    max_addresses = feed.get('max_addresses', DEFAULT_MAX_ADDRESSES)

    if raw_count < min_entries:
        return False, (
            f"Feed {feed['name']}: only {raw_count} raw lines (expected >= {min_entries}). "
            f"Possible truncated download or upstream change."
        )

    if len(entries) > max_entries:
        return False, (
            f"Feed {feed['name']}: {len(entries)} entries exceeds maximum {max_entries}. "
            f"Possible upstream data corruption or format change."
        )

    if len(entries) == 0:
        if feed.get('allow_empty', False):
            # Live C2 lists really do empty out. Nothing to merge, but this is
            # not a feed failure and must not count as one.
            log.info(f"Feed {feed['name']}: empty, which this feed is allowed to be")
            return True, None
        return False, f"Feed {feed['name']}: zero valid entries after parsing. Rejecting update."

    # Breadth check. Entry counts alone miss the case where a feed swaps /32s
    # for /16s: the line count barely moves while coverage grows a thousandfold.
    covered = sum(entry.ip.num_addresses for entry in entries)
    if covered > max_addresses:
        return False, (
            f"Feed {feed['name']}: {len(entries)} entries cover {covered:,} addresses, "
            f"over the {max_addresses:,} cap. Possible upstream format change — "
            f"rejecting rather than blackholing that much space."
        )
    log.info(f"{feed['name']}: covers {covered:,} addresses (cap {max_addresses:,})")

    # Sanity check: verify entries contain valid IP data
    sample_size = min(100, len(entries))
    sample = entries[:sample_size]
    for entry in sample:
        if not isinstance(entry.ip, ipaddress.IPv4Network):
            return False, f"Feed {feed['name']}: invalid IP type in parsed entries"
        if not (1 <= entry.score <= 127):
            return False, f"Feed {feed['name']}: score {entry.score} outside valid range [1,127]"
        if not (1 <= entry.category <= 255):
            return False, f"Feed {feed['name']}: category {entry.category} outside valid range [1,255]"

    return True, None


# ---------------------------------------------------------------------------
# Deduplication and merging
# ---------------------------------------------------------------------------

def merge_entries(all_feed_results: List[FeedResult]) -> List[Tuple[ipaddress.IPv4Network, int, int]]:
    """
    Merge entries from multiple feeds, deduplicating per (network, category).

    Suricata's reputation format allows several category lines for the same
    address:

        1.1.1.1,1,10
        1.1.1.1,2,10

    So an address seen in three feeds keeps all three categories, and the
    multi-source rules (sid 9100070-9100072) can each match it. Keying on the
    address alone instead, and keeping only the top-scoring feed's category,
    silently discarded every other category that address belonged to, which
    is exactly what those rules depend on.

    Within one category the highest score wins.

    Returns a sorted list of (network, category, score) tuples.
    """
    # Key: (str(network), category) — maps to max_score
    merged: Dict[Tuple[str, int], int] = {}

    total_raw = 0
    for result in all_feed_results:
        if not result.download_ok or not result.validation_ok:
            continue
        for entry in result.entries:
            total_raw += 1
            key = (str(entry.ip), entry.category)
            existing = merged.get(key)
            if existing is None or entry.score > existing:
                merged[key] = entry.score

    # IPv4Network, not ip_network(): parse_feed_file drops IPv6, so every key
    # here is v4 and the narrower type keeps the annotation honest.
    result_list = [
        (ipaddress.IPv4Network(net_str), category, score)
        for (net_str, category), score in merged.items()
    ]
    # Sort so the output file is stable between runs and diffable.
    result_list.sort(key=lambda item: (item[0].network_address, item[0].prefixlen, item[1]))

    distinct_ips = len({net for net, _, _ in result_list})
    log.info(
        f"Merged {total_raw:,} raw entries to {len(result_list):,} lines "
        f"covering {distinct_ips:,} distinct networks "
        f"({len(result_list) - distinct_ips:,} multi-category)"
    )
    return result_list


# ---------------------------------------------------------------------------
# Writing the reputation file
# ---------------------------------------------------------------------------

def write_reputation_file(
    entries: List[Tuple[ipaddress.IPv4Network, int, int]],
    dest_path: Path,
) -> Tuple[bool, Optional[str]]:
    """
    Write merged entries to the Suricata reputation file format.

    Suricata iprep format: <IP_OR_CIDR>,<category_id>,<score>
    One entry per line. Suricata 7.x supports CIDR notation directly.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=dest_path.parent,
            prefix='.rep_tmp_',
            delete=False
        ) as tmp:
            tmp_path = tmp.name

            tmp.write(f"# Suricata IP Reputation File\n")
            tmp.write(f"# Generated: {datetime.now(timezone.utc).isoformat()}\n")
            tmp.write(f"# Total entries: {len(entries)}\n")
            tmp.write(f"# Format: IP_OR_CIDR,category_id,score\n")
            tmp.write("#\n")

            for net, category, score in entries:
                # For /32 hosts, write just the IP without prefix notation
                if net.prefixlen == 32:
                    tmp.write(f"{net.network_address},{category},{score}\n")
                else:
                    tmp.write(f"{net},{category},{score}\n")

        # Atomic rename
        os.replace(tmp_path, dest_path)
        log.info(f"Wrote {len(entries):,} entries to {dest_path}")
        return True, None

    except Exception as e:
        # Clean up temp file if it exists
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False, str(e)


def write_whitelist_reputation_file(
    whitelist: Set[ipaddress.IPv4Network],
    dest_path: Path,
) -> Tuple[bool, Optional[str]]:
    """
    Write whitelist.txt out as reputation category 15.

    Filtering whitelisted addresses out of reputation.list already stops them
    being dropped by our own feeds. This file is the second layer: it gives the
    pass rule (sid 9100090) something to match, so a trusted address is let
    through even if it reaches a drop rule by some other route. Without it that
    rule matches nothing regardless of how the whitelist is maintained.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=dest_path.parent,
            prefix='.wl_tmp_',
            delete=False
        ) as tmp:
            tmp_path = tmp.name

            tmp.write("# Suricata IP Reputation File — whitelist (category "
                      f"{CATEGORY_WHITELIST})\n")
            tmp.write(f"# Generated: {datetime.now(timezone.utc).isoformat()}\n")
            tmp.write(f"# Total entries: {len(whitelist)}\n")
            tmp.write("# Source: whitelist.txt. Do not edit by hand.\n")
            tmp.write("#\n")

            score = SOURCE_SCORE["whitelist"]
            for net in sorted(whitelist, key=lambda n: (n.network_address, n.prefixlen)):
                if net.prefixlen == 32:
                    tmp.write(f"{net.network_address},{CATEGORY_WHITELIST},{score}\n")
                else:
                    tmp.write(f"{net},{CATEGORY_WHITELIST},{score}\n")

        os.replace(tmp_path, dest_path)
        log.info(f"Wrote {len(whitelist):,} whitelist entries to {dest_path}")
        return True, None

    except Exception as e:
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False, str(e)


def write_serve_files(
    feed_results: List[FeedResult],
    merged: List[Tuple[ipaddress.IPv4Network, int, int]],
    serve_dir: Path,
) -> Tuple[bool, Optional[str]]:
    """
    Write the plain CIDR-per-line files that blocklist_server.py serves and
    OPNsense pulls as URL table aliases.

    Nothing wrote these before, so the alias stayed empty forever and the
    server answered 404 on every request. The names are fixed by
    blocklist_server.py's route table:

      recommended.txt — Data-Shield RECOMMENDED, whitelist-filtered
      aggressive.txt  — Data-Shield CRITICAL, whitelist-filtered
      combined.txt    — every network in reputation.list, one line each
    """
    by_feed = {r.feed_name: r for r in feed_results if r.validation_ok}

    def networks_of(feed_name: str) -> List[ipaddress.IPv4Network]:
        result = by_feed.get(feed_name)
        if result is None:
            return []
        return sorted(
            {entry.ip for entry in result.entries},
            key=lambda n: (n.network_address, n.prefixlen),
        )

    combined = sorted({net for net, _, _ in merged},
                      key=lambda n: (n.network_address, n.prefixlen))

    payloads = {
        "recommended.txt": networks_of("datashield_recommended"),
        "aggressive.txt": networks_of("datashield_aggressive"),
        "combined.txt": combined,
    }

    try:
        serve_dir.mkdir(parents=True, exist_ok=True)
        for filename, networks in payloads.items():
            dest = serve_dir / filename
            if not networks:
                # Leave the previous file in place. An empty alias table means
                # OPNsense stops blocking, so a failed feed must not flush it.
                log.warning(f"No entries for {filename} — leaving existing file untouched")
                continue
            with tempfile.NamedTemporaryFile(
                mode='w', dir=serve_dir, prefix=f'.{filename}_', delete=False
            ) as tmp:
                tmp_path = tmp.name
                tmp.write(f"# Generated: {datetime.now(timezone.utc).isoformat()}\n")
                tmp.write(f"# Entries: {len(networks)}\n")
                for net in networks:
                    tmp.write(f"{net.network_address}\n" if net.prefixlen == 32 else f"{net}\n")
            os.replace(tmp_path, dest)
            log.info(f"Wrote {len(networks):,} entries to {dest}")
        return True, None

    except Exception as e:
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False, str(e)


# ---------------------------------------------------------------------------
# Suricata reload
# ---------------------------------------------------------------------------

def reload_suricata_iprep(socket_path: str) -> Tuple[bool, Optional[str]]:
    """
    Signal Suricata to reload its IP reputation database via the management socket.
    Uses suricatasc command if socket exists, otherwise sends SIGUSR2.
    """
    # Try suricatasc first (cleaner, gives us confirmation)
    try:
        result = subprocess.run(
            ["suricatasc", "-c", "reload-rules"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("Suricata rules/iprep reloaded via suricatasc")
            return True, None
        else:
            log.warning(f"suricatasc reload returned {result.returncode}: {result.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug(f"suricatasc not available or timed out: {e}")

    # Fallback: SIGUSR2 triggers a live rule reload in Suricata
    try:
        result = subprocess.run(
            ["pkill", "-USR2", "suricata"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            log.info("Sent SIGUSR2 to Suricata for iprep reload")
            return True, None
        else:
            return False, f"pkill -USR2 suricata returned {result.returncode}: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "Timeout sending SIGUSR2 to Suricata"


# ---------------------------------------------------------------------------
# Checksum and rollback support
# ---------------------------------------------------------------------------

def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def backup_reputation_file(src: Path, backup: Path) -> bool:
    """Create backup of current reputation file before updating."""
    if src.exists():
        shutil.copy2(src, backup)
        log.info(f"Backed up {src} to {backup}")
        return True
    return False


def rollback_reputation_file(backup: Path, dest: Path) -> bool:
    """Restore reputation file from backup."""
    if backup.exists():
        shutil.copy2(backup, dest)
        log.warning(f"Rolled back {dest} from {backup}")
        return True
    log.error(f"Rollback requested but backup {backup} does not exist")
    return False


# ---------------------------------------------------------------------------
# Metadata tracking
# ---------------------------------------------------------------------------

def write_metadata(
    metadata_path: Path,
    feed_results: List[FeedResult],
    merged_count: int,
    sha256: str,
    category_counts: Optional[Dict[int, int]] = None,
) -> None:
    meta = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "merged_entry_count": merged_count,
        "reputation_file_sha256": sha256,
        # Lines written per category. A category referenced by a rule but
        # missing here means that rule cannot match this cycle.
        "category_counts": {str(k): v for k, v in sorted((category_counts or {}).items())},
        "feeds": [
            {
                "name": r.feed_name,
                "entry_count": r.entry_count,
                "download_ok": r.download_ok,
                "validation_ok": r.validation_ok,
                "error": r.error,
            }
            for r in feed_results
        ],
    }
    with open(metadata_path, 'w') as f:
        json.dump(meta, f, indent=2)
    log.info(f"Metadata written to {metadata_path}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, force: bool = False) -> int:
    """
    Main update routine.
    Returns 0 on success, 1 on partial failure, 2 on complete failure.
    """
    # Ensure directories exist
    IPREP_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    SERVE_DIR.mkdir(parents=True, exist_ok=True)

    # Load whitelist
    whitelist = load_whitelist(WHITELIST_PATH)

    # Process each feed
    feed_results: List[FeedResult] = []
    successful_feeds = 0

    for feed in FEEDS:
        if not feed.get('enabled', True):
            log.info(f"Skipping disabled feed: {feed['name']}")
            continue

        log.info(f"Processing feed: {feed['name']}")

        # Download
        ok, filepath, err = download_feed(feed, DOWNLOAD_DIR)
        if not ok:
            log.error(f"Download failed for {feed['name']}: {err}")
            feed_results.append(FeedResult(
                feed_name=feed['name'],
                entries=[],
                entry_count=0,
                download_ok=False,
                validation_ok=False,
                error=err,
            ))
            continue

        # Parse
        assert filepath is not None
        entries, raw_count, err = parse_feed_file(filepath, feed, whitelist)
        if err:
            log.error(f"Parse error for {feed['name']}: {err}")
            feed_results.append(FeedResult(
                feed_name=feed['name'],
                entries=[],
                entry_count=raw_count,
                download_ok=True,
                validation_ok=False,
                error=err,
            ))
            continue

        # Validate
        valid, err = validate_feed(entries, feed, raw_count)
        if not valid:
            log.error(f"Validation failed for {feed['name']}: {err}")
            feed_results.append(FeedResult(
                feed_name=feed['name'],
                entries=[],
                entry_count=raw_count,
                download_ok=True,
                validation_ok=False,
                error=err,
            ))
            continue

        feed_results.append(FeedResult(
            feed_name=feed['name'],
            entries=entries,
            entry_count=len(entries),
            download_ok=True,
            validation_ok=True,
            error=None,
        ))
        successful_feeds += 1
        log.info(f"Feed {feed['name']}: {len(entries):,} entries validated OK")

    if successful_feeds == 0:
        log.error("All feeds failed — aborting update to avoid deploying empty blocklist")
        return 2

    enabled_feeds = len([f for f in FEEDS if f.get('enabled', True)])
    if successful_feeds < enabled_feeds:
        log.warning(
            f"Only {successful_feeds}/{enabled_feeds} enabled feeds succeeded — "
            f"continuing with partial update"
        )

    # Merge
    merged = merge_entries(feed_results)

    if len(merged) == 0:
        log.error("No entries after merging — aborting update")
        return 2

    if dry_run:
        log.info(f"DRY RUN: would write {len(merged):,} entries to {REPUTATION_FILE}")
        log.info(f"DRY RUN: would write {len(whitelist):,} entries to {WHITELIST_REPUTATION_FILE}")
        log.info(f"DRY RUN: would refresh the served blocklists under {SERVE_DIR}")
        return 0

    # Backup current file
    backup_reputation_file(REPUTATION_FILE, REPUTATION_FILE_BACKUP)

    # Write new reputation file
    write_ok, write_err = write_reputation_file(merged, REPUTATION_FILE)
    if not write_ok:
        log.error(f"Failed to write reputation file: {write_err}")
        rollback_reputation_file(REPUTATION_FILE_BACKUP, REPUTATION_FILE)
        return 2

    # Whitelist file — the pass rule has no data without it
    wl_ok, wl_err = write_whitelist_reputation_file(whitelist, WHITELIST_REPUTATION_FILE)
    if not wl_ok:
        log.error(f"Failed to write whitelist reputation file: {wl_err}")
        rollback_reputation_file(REPUTATION_FILE_BACKUP, REPUTATION_FILE)
        return 2

    # Plain lists for OPNsense URL table aliases
    serve_ok, serve_err = write_serve_files(feed_results, merged, SERVE_DIR)
    if not serve_ok:
        # The Suricata side is already deployed and valid, so this is not a
        # rollback: the firewall alias just keeps its previous contents.
        log.error(f"Failed to write served blocklists: {serve_err}")

    # Compute checksum for metadata
    sha256 = compute_sha256(REPUTATION_FILE)

    # Write metadata, including the per-category line counts so an operator
    # can see at a glance that a rule's category is populated.
    category_counts: Dict[int, int] = {}
    for _, category, _ in merged:
        category_counts[category] = category_counts.get(category, 0) + 1
    category_counts[CATEGORY_WHITELIST] = len(whitelist)
    write_metadata(REPUTATION_METADATA, feed_results, len(merged), sha256, category_counts)

    # Reload Suricata
    reload_ok, reload_err = reload_suricata_iprep(SURICATA_SOCKET)
    if not reload_ok:
        log.error(f"Suricata reload failed: {reload_err}")
        # Not rolling back — the file update is valid; just reload failed.
        # Suricata will pick up the file on next restart or manual reload.
        return 1

    log.info(f"iprep update complete: {len(merged):,} entries deployed")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Update Suricata IP reputation database from threat intelligence feeds")
    parser.add_argument("--dry-run", action="store_true", help="Download and validate but do not write or reload")
    parser.add_argument("--force", action="store_true", help="Force update even if checksums match")
    parser.add_argument("--loglevel", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    global log
    log = setup_logging(args.loglevel)

    log.info("=== iprep-update starting ===")
    log.info(f"IPREP_DIR: {IPREP_DIR}")
    log.info(f"STAGING_DIR: {STAGING_DIR}")
    log.info(f"Dry run: {args.dry_run}")

    exit_code = run(dry_run=args.dry_run, force=args.force)
    log.info(f"=== iprep-update finished (exit code {exit_code}) ===")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
