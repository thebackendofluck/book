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
sanctions_checker.py — OFAC SDN list downloader with fuzzy name matching.

Downloads the OFAC Specially Designated Nationals (SDN) XML list, parses it
into a Redis-cached name index, and exposes match/check methods that use
fuzzywuzzy token_sort_ratio for name comparison.

OFAC SDN list URL (public, no auth required):
    https://www.treasury.gov/ofac/downloads/sdn.xml

Cache strategy:
  - Full name list stored as a Redis SET (key per normalised alias token).
  - Metadata per SDN entry stored as a JSON hash.
  - List refreshed every REFRESH_INTERVAL_HOURS (default 24 hours).
  - Download timestamp tracked so concurrent workers do not double-fetch.

No hardcoded secrets.  Redis URL from REDIS_URL env var.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
import structlog
from fuzzywuzzy import fuzz  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

OFAC_SDN_URL: str = os.environ.get(
    "OFAC_SDN_URL",
    "https://www.treasury.gov/ofac/downloads/sdn.xml",
)
OFAC_ALT_URL: str = os.environ.get(
    "OFAC_ALT_URL",
    "https://www.treasury.gov/ofac/downloads/sdn_advanced.xml",
)

REFRESH_INTERVAL_HOURS: int = int(os.environ.get("OFAC_REFRESH_HOURS", "24"))
FUZZY_MATCH_THRESHOLD: int = int(os.environ.get("OFAC_FUZZY_THRESHOLD", "85"))

REDIS_SDN_ENTRIES_KEY = "sanctions:sdn:entries"      # hash: uid -> JSON blob
REDIS_SDN_NAME_INDEX = "sanctions:sdn:name_tokens"   # sorted set: token -> score (alphabetic sort)
REDIS_SDN_REFRESH_KEY = "sanctions:sdn:last_refresh"
REDIS_SDN_COUNT_KEY = "sanctions:sdn:entry_count"
REDIS_SDN_VERSION_KEY = "sanctions:sdn:xml_hash"

OFAC_NS = "http://tempuri.org/sdnList.xsd"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SDNEntry:
    uid: str
    name: str                        # primary name
    aliases: list[str]               # AKA names
    entity_type: str                 # Individual / Entity / Aircraft / Vessel
    program: str                     # SDGT, NPWMD, IRAN, etc.
    nationality: str
    dob: str
    remarks: str

    def all_names(self) -> list[str]:
        return [self.name] + self.aliases


@dataclass
class SanctionsMatch:
    matched: bool
    score: int                       # 0-100 fuzzy match score
    matched_name: str                # the alias that triggered the match
    query_name: str
    entry: Optional[SDNEntry] = None
    threshold: int = FUZZY_MATCH_THRESHOLD

    @property
    def is_high_confidence(self) -> bool:
        return self.score >= 95

    @property
    def is_review_required(self) -> bool:
        return self.threshold - 10 <= self.score < self.threshold


@dataclass
class SanctionsCheckResult:
    query_name: str
    is_match: bool
    best_match: Optional[SanctionsMatch] = None
    all_matches: list[SanctionsMatch] | None = None
    checked_at: float = 0.0

    def __post_init__(self) -> None:
        if self.checked_at == 0.0:
            self.checked_at = time.time()
        if self.all_matches is None:
            self.all_matches = []


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class SanctionsChecker:
    """
    OFAC SDN list checker with fuzzy name matching and Redis cache.

    Usage:
        checker = SanctionsChecker()
        checker.refresh_if_stale()          # downloads + caches if > 24 h old
        result = checker.check("Vladimir Putin")
        if result.is_match:
            print(result.best_match.entry.program)
    """

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        threshold: int = FUZZY_MATCH_THRESHOLD,
        refresh_hours: int = REFRESH_INTERVAL_HOURS,
    ) -> None:
        import redis as redis_lib

        self._redis = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        self.threshold = threshold
        self.refresh_hours = refresh_hours

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        name: str,
        max_candidates: int = 50,
    ) -> SanctionsCheckResult:
        """
        Check a name against the SDN list.

        Candidate generation:
          1. Normalise the query name to ASCII tokens.
          2. Look up each token in the Redis name index (exact prefix scan).
          3. For each candidate UID, load the SDN entry and run fuzzy match
             against every alias.

        Args:
            name:           Full name to check (first + last, or entity name).
            max_candidates: Limit the fuzzy comparison set to avoid O(n) scans.

        Returns:
            SanctionsCheckResult with best match and all matches above threshold.
        """
        if not name or not name.strip():
            return SanctionsCheckResult(query_name=name, is_match=False)

        query_norm = _normalise_name(name)
        tokens = query_norm.split()

        # Gather candidate UIDs via token lookup
        candidate_uids: set[str] = set()
        for token in tokens:
            if len(token) < 3:
                continue
            # ZSCAN for members whose score-sorted value starts with token
            uids = self._redis.smembers(f"sanctions:sdn:tok:{token[:6]}")
            candidate_uids.update(uids)
            if len(candidate_uids) >= max_candidates * 3:
                break

        if not candidate_uids:
            # Fall back to full scan of a limited subset when no token index hit
            candidate_uids = self._full_scan_candidates(query_norm, max_candidates)

        matches: list[SanctionsMatch] = []

        for uid in list(candidate_uids)[:max_candidates]:
            raw = self._redis.hget(REDIS_SDN_ENTRIES_KEY, uid)
            if not raw:
                continue
            try:
                entry = SDNEntry(**json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue

            best_score = 0
            best_alias = ""
            for alias in entry.all_names():
                alias_norm = _normalise_name(alias)
                score = fuzz.token_sort_ratio(query_norm, alias_norm)
                if score > best_score:
                    best_score = score
                    best_alias = alias

            if best_score >= self.threshold - 10:  # include near-misses for REVIEW
                matches.append(SanctionsMatch(
                    matched=(best_score >= self.threshold),
                    score=best_score,
                    matched_name=best_alias,
                    query_name=name,
                    entry=entry,
                    threshold=self.threshold,
                ))

        matches.sort(key=lambda m: m.score, reverse=True)
        confirmed = [m for m in matches if m.matched]

        best = matches[0] if matches else None
        is_match = bool(confirmed)

        if is_match or (best and best.score >= self.threshold - 10):
            logger.warning(
                "sanctions_check_hit",
                query=name,
                best_score=best.score if best else 0,
                matched=is_match,
            )
        else:
            logger.debug("sanctions_check_clean", query=name)

        return SanctionsCheckResult(
            query_name=name,
            is_match=is_match,
            best_match=best,
            all_matches=matches,
        )

    def check_multiple(self, names: list[str]) -> list[SanctionsCheckResult]:
        """Check a list of names (e.g. maiden name + married name)."""
        results = []
        for name in names:
            r = self.check(name)
            results.append(r)
            if r.is_match:
                break  # short-circuit on first confirmed match
        return results

    # ------------------------------------------------------------------
    # Cache refresh
    # ------------------------------------------------------------------

    def refresh_if_stale(self) -> bool:
        """
        Download and cache the OFAC SDN list if older than refresh_hours.

        Returns True if a refresh was performed, False if still fresh.
        """
        last_str = self._redis.get(REDIS_SDN_REFRESH_KEY)
        if last_str:
            last = float(last_str)
            age_hours = (time.time() - last) / 3600
            if age_hours < self.refresh_hours:
                logger.debug("sanctions_cache_fresh", age_hours=round(age_hours, 1))
                return False

        logger.info("sanctions_cache_stale", url=OFAC_SDN_URL)
        return self.force_refresh()

    def force_refresh(self) -> bool:
        """Download the OFAC SDN list and rebuild the Redis cache."""
        xml_bytes = _download_ofac_xml(OFAC_SDN_URL)
        if not xml_bytes:
            logger.error("sanctions_download_failed", url=OFAC_SDN_URL)
            return False

        xml_hash = hashlib.sha256(xml_bytes).hexdigest()
        cached_hash = self._redis.get(REDIS_SDN_VERSION_KEY)
        if cached_hash == xml_hash:
            # Content unchanged — just reset the refresh timestamp
            self._redis.set(REDIS_SDN_REFRESH_KEY, str(time.time()))
            logger.info("sanctions_xml_unchanged", hash=xml_hash[:12])
            return False

        entries = _parse_ofac_xml(xml_bytes)
        self._cache_entries(entries)
        self._redis.set(REDIS_SDN_VERSION_KEY, xml_hash)
        self._redis.set(REDIS_SDN_REFRESH_KEY, str(time.time()))
        self._redis.set(REDIS_SDN_COUNT_KEY, str(len(entries)))

        logger.info(
            "sanctions_cache_updated",
            entries=len(entries),
            hash=xml_hash[:12],
        )
        return True

    def cache_stats(self) -> dict:
        return {
            "entry_count": int(self._redis.get(REDIS_SDN_COUNT_KEY) or 0),
            "last_refresh": float(self._redis.get(REDIS_SDN_REFRESH_KEY) or 0),
            "xml_hash": (self._redis.get(REDIS_SDN_VERSION_KEY) or "")[:16],
            "threshold": self.threshold,
        }

    # ------------------------------------------------------------------
    # Cache building
    # ------------------------------------------------------------------

    def _cache_entries(self, entries: list[SDNEntry]) -> None:
        """Write all SDN entries to Redis in batches."""
        pipe = self._redis.pipeline(transaction=False)
        batch_size = 500
        count = 0

        # Flush old data
        old_keys = self._redis.keys("sanctions:sdn:tok:*")
        if old_keys:
            self._redis.delete(*old_keys)
        self._redis.delete(REDIS_SDN_ENTRIES_KEY)

        for entry in entries:
            pipe.hset(REDIS_SDN_ENTRIES_KEY, entry.uid, json.dumps(asdict(entry)))

            # Build token index: first 6 chars of each token -> set of UIDs
            for alias in entry.all_names():
                norm = _normalise_name(alias)
                for token in norm.split():
                    if len(token) >= 3:
                        key = f"sanctions:sdn:tok:{token[:6]}"
                        pipe.sadd(key, entry.uid)
                        pipe.expire(key, 86400 * (self.refresh_hours + 2))

            count += 1
            if count % batch_size == 0:
                pipe.execute()
                pipe = self._redis.pipeline(transaction=False)

        pipe.execute()
        logger.debug("sanctions_entries_cached", count=count)

    def _full_scan_candidates(
        self, query_norm: str, max_candidates: int
    ) -> set[str]:
        """
        Last-resort full scan of the entry hash when token index is empty.
        Returns up to max_candidates UIDs with any name overlap.
        """
        query_tokens = set(query_norm.split())
        candidates: set[str] = set()

        cursor = "0"
        while True:
            cursor, items = self._redis.hscan(
                REDIS_SDN_ENTRIES_KEY, cursor=cursor, count=200  # type: ignore[arg-type]
            )
            for uid, raw in items.items():
                try:
                    data = json.loads(raw)
                    all_names = [data.get("name", "")] + data.get("aliases", [])
                    for n in all_names:
                        tokens = set(_normalise_name(n).split())
                        if tokens & query_tokens:
                            candidates.add(uid)
                            break
                except (json.JSONDecodeError, KeyError):
                    pass
                if len(candidates) >= max_candidates:
                    return candidates
            if cursor == "0":
                break

        return candidates


# ---------------------------------------------------------------------------
# OFAC XML parser
# ---------------------------------------------------------------------------

def _parse_ofac_xml(xml_bytes: bytes) -> list[SDNEntry]:
    """
    Parse the OFAC SDN XML into a list of SDNEntry objects.

    The OFAC SDN XML schema uses the namespace:
        http://tempuri.org/sdnList.xsd
    Each <sdnEntry> contains:
        <uid>, <lastName>, <firstName>, <sdnType>, <programList>,
        <akaList> (optional), <dateOfBirthList>, <nationalityList>
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.error("ofac_xml_parse_error", error=str(exc))
        return []

    ns = {"s": OFAC_NS}
    entries: list[SDNEntry] = []

    for sdn_node in root.findall("s:sdnEntry", ns):
        uid = _xml_text(sdn_node, "s:uid", ns)
        last_name = _xml_text(sdn_node, "s:lastName", ns)
        first_name = _xml_text(sdn_node, "s:firstName", ns)
        entity_type = _xml_text(sdn_node, "s:sdnType", ns)

        # Primary name: "LAST_NAME, FIRST_NAME" for individuals; just lastName for entities
        if first_name:
            primary_name = f"{last_name}, {first_name}"
        else:
            primary_name = last_name

        # Programs
        programs: list[str] = [
            _xml_text(prog, "s:program", ns)
            for prog in sdn_node.findall("s:programList/s:program", ns)
        ]

        # AKA aliases
        aliases: list[str] = []
        for aka in sdn_node.findall("s:akaList/s:aka", ns):
            ak_last = _xml_text(aka, "s:lastName", ns)
            ak_first = _xml_text(aka, "s:firstName", ns)
            aka_type = _xml_text(aka, "s:type", ns)
            if ak_last or ak_first:
                if ak_first:
                    aliases.append(f"{ak_last}, {ak_first}")
                else:
                    aliases.append(ak_last)
            # Also store category (weak/strong) in remarks could be useful; skip for now

        # Date of birth
        dob_parts: list[str] = []
        for dob_node in sdn_node.findall("s:dateOfBirthList/s:dateOfBirthItem", ns):
            dob_parts.append(_xml_text(dob_node, "s:dateOfBirth", ns))
        dob = "; ".join(filter(None, dob_parts))

        # Nationality
        nats: list[str] = []
        for nat in sdn_node.findall("s:nationalityList/s:nationality", ns):
            nats.append(_xml_text(nat, "s:country", ns))
        nationality = "; ".join(filter(None, nats))

        # Remarks
        remarks = _xml_text(sdn_node, "s:remarks", ns)

        entry = SDNEntry(
            uid=uid,
            name=primary_name,
            aliases=aliases,
            entity_type=entity_type,
            program="; ".join(filter(None, programs)),
            nationality=nationality,
            dob=dob,
            remarks=remarks,
        )
        entries.append(entry)

    return entries


def _xml_text(node: ET.Element, path: str, ns: dict) -> str:
    el = node.find(path, ns)
    if el is not None and el.text:
        return el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_ofac_xml(url: str, timeout: int = 60) -> bytes | None:
    """Download the OFAC SDN XML. Returns raw bytes or None on failure."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        logger.error("ofac_download_http_error", url=url, error=str(exc))
        return None
    except Exception as exc:
        logger.error("ofac_download_unexpected_error", url=url, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def _normalise_name(name: str) -> str:
    """
    Normalise a name for fuzzy comparison:
      1. Unicode → ASCII (NFKD decomposition, drop combining marks).
      2. Lowercase.
      3. Remove punctuation except spaces.
      4. Collapse whitespace.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    lower = ascii_str.lower()
    clean = re.sub(r"[^a-z0-9 ]", " ", lower)
    return " ".join(clean.split())
