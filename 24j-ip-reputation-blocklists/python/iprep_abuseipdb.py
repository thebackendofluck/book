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
iprep_abuseipdb.py — AbuseIPDB integration, bulk and per-IP.

Two entry points:

  download_blacklist() — pulls the bulk blacklist to a file in the same plain
    one-address-per-line format as every other feed. iprep_update.py calls
    this for the abuseipdb_blacklist feed, which is what populates category 12
    for rule 9100080.

  check_and_add_to_iprep() — per-IP lookup for addresses that trip
    application-layer anomaly detection but are not already in the blocklist.

Both require the ABUSEIPDB_API_KEY environment variable. The bulk endpoint is
rate limited per plan, so it runs on the 4-hour iprep-update cycle rather than
on demand.
"""

import ipaddress
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("iprep-abuseipdb")

ABUSEIPDB_API_KEY = os.environ.get("ABUSEIPDB_API_KEY", "")
ABUSEIPDB_MIN_CONFIDENCE = 90
ABUSEIPDB_BLACKLIST_URL = "https://api.abuseipdb.com/api/v2/blacklist"

# These must match values in iprep_update.py
CATEGORY_ABUSEIPDB = 12


def download_blacklist(
    dest_path: Path,
    confidence_minimum: int = ABUSEIPDB_MIN_CONFIDENCE,
    limit: int = 10000,
    timeout: int = 60,
) -> Tuple[bool, Optional[str]]:
    """
    Fetch the AbuseIPDB bulk blacklist to dest_path, one address per line.

    Returns (success, error_message). The plaintext response format is already
    what iprep_update.parse_feed_file expects, so no conversion is needed.

    The limit is capped by the account plan: the free tier serves 10k
    addresses, paid plans more. Asking for more than the plan allows returns
    what the plan allows rather than an error.
    """
    if not ABUSEIPDB_API_KEY:
        return False, "ABUSEIPDB_API_KEY is not set"

    url = (
        f"{ABUSEIPDB_BLACKLIST_URL}"
        f"?confidenceMinimum={confidence_minimum}&limit={limit}&plaintext"
    )
    req = Request(url, headers={
        "Key": ABUSEIPDB_API_KEY,
        "Accept": "text/plain",
    })

    try:
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    except HTTPError as e:
        # 401 means a missing or revoked key, 429 means the daily quota is
        # spent. Both must fail the feed rather than yield an empty file: an
        # empty file would look like a legitimately clean blacklist.
        return False, f"AbuseIPDB blacklist HTTP {e.code}: {e.reason}"
    except URLError as e:
        return False, f"AbuseIPDB blacklist download failed: {e}"

    if not content.strip():
        return False, "AbuseIPDB blacklist returned an empty body"

    try:
        with tempfile.NamedTemporaryFile(
            mode='wb', dir=dest_path.parent, prefix='.abuseipdb_', delete=False
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(content)
        os.replace(tmp_path, dest_path)
    except OSError as e:
        return False, f"Could not write AbuseIPDB blacklist to {dest_path}: {e}"

    log.info(f"AbuseIPDB blacklist: {len(content):,} bytes written to {dest_path}")
    return True, None


def query_abuseipdb(ip: str, max_age_days: int = 30) -> dict:
    """
    Query AbuseIPDB for a single IP.
    Returns the API response dict, or empty dict on failure.
    """
    if not ABUSEIPDB_API_KEY:
        return {}

    url = f"https://api.abuseipdb.com/api/v2/check?ipAddress={ip}&maxAgeInDays={max_age_days}&verbose"
    req = Request(url, headers={
        "Key": ABUSEIPDB_API_KEY,
        "Accept": "application/json",
    })

    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.debug(f"AbuseIPDB query failed for {ip}: {e}")
        return {}


def check_and_add_to_iprep(ip: str, iprep_entries: list) -> bool:
    """
    Check an IP against AbuseIPDB and add to iprep if high confidence.
    Called for IPs triggering application-layer anomaly detection
    that are not already in the blocklist.
    Returns True if the IP was added.
    """
    result = query_abuseipdb(ip)
    if not result:
        return False

    data = result.get("data", {})
    confidence = data.get("abuseConfidenceScore", 0)

    if confidence >= ABUSEIPDB_MIN_CONFIDENCE:
        net = ipaddress.ip_network(ip + "/32", strict=False)
        # Import ReputationEntry at runtime to avoid circular dependency
        # when used standalone; caller passes a plain list and appends manually
        entry = {
            "ip": net,
            "category": CATEGORY_ABUSEIPDB,
            "score": min(127, int(confidence * 1.27)),  # Map 0-100 to 1-127
            "source": "abuseipdb_realtime",
        }
        iprep_entries.append(entry)
        log.info(f"Added {ip} to iprep from AbuseIPDB (confidence={confidence})")
        return True

    return False
