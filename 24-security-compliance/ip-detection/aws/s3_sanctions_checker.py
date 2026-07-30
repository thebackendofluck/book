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
s3_sanctions_checker.py
-----------------------
OFAC SDN (Specially Designated Nationals) list checker with:
  - S3 storage for the SDN XML + a scheduled refresh Lambda
  - In-memory parsed index loaded from S3 on Lambda cold start
  - Fuzzy name matching via Levenshtein distance (python-Levenshtein / rapidfuzz)
  - Name normalisation: strip diacritics, expand aliases, handle transliterations
  - Date-of-birth corroboration when available
  - PEP (Politically Exposed Persons) list support via same interface

OFAC SDN XML: https://www.treasury.gov/ofac/downloads/sdn.xml
Advanced SDN: https://www.treasury.gov/ofac/downloads/sdn_advanced.xml (recommended)

Scheduled refresh:
  A separate Lambda (scheduled via EventBridge) should call SanctionsChecker.refresh()
  to re-download and re-index the SDN list whenever OFAC publishes updates.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn_advanced.xml"
LEVENSHTEIN_THRESHOLD = 0.80       # minimum similarity score for a REVIEW match
BLOCK_THRESHOLD = 0.90             # similarity score that triggers a BLOCK
MAX_RESULTS = 10                   # max fuzzy matches to return
DOB_CORROBORATION_BONUS = 0.05     # score boost when DOB matches


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SDNEntry:
    uid: str
    sdn_type: str                  # INDIVIDUAL | ENTITY | VESSEL | AIRCRAFT
    program: str                   # e.g. SDGT, IRAN, DPRK, RUSSIA
    names: list[str] = field(default_factory=list)          # primary + aliases
    dob_strings: list[str] = field(default_factory=list)    # "1980 Jan 15", etc.
    nationalities: list[str] = field(default_factory=list)  # ISO-2 codes
    remarks: str = ""
    list_type: str = "OFAC_SDN"


@dataclass
class SanctionsMatch:
    uid: str
    matched_name: str
    query_name: str
    score: float
    sdn_type: str
    program: str
    list_type: str
    dob_matched: bool = False
    nationality_matched: bool = False


# ---------------------------------------------------------------------------
# Name normalisation utilities
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "al", "el", "bin", "bint", "ibn", "abu", "bou", "von", "van",
    "de", "la", "le", "du", "des", "the", "a", "an",
})


def _normalise_name(name: str) -> str:
    """
    Normalise a name for comparison:
    1. Unicode NFKD → strip diacritics
    2. Lowercase
    3. Remove punctuation / extra whitespace
    4. Strip leading/trailing stop-words
    """
    if not name:
        return ""
    # Strip diacritics
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    # Lowercase + remove non-alpha-space
    cleaned = re.sub(r"[^a-z\s]", " ", ascii_name.lower())
    # Collapse whitespace
    tokens = cleaned.split()
    # Remove stop words only if there are other tokens
    filtered = [t for t in tokens if t not in _STOP_WORDS] if len(tokens) > 1 else tokens
    return " ".join(filtered)


def _name_tokens(name: str) -> frozenset[str]:
    return frozenset(_normalise_name(name).split())


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------

def _compute_similarity(query: str, candidate: str) -> float:
    """
    Compute a similarity score in [0, 1] between two normalised names.
    Strategy:
    1. Exact match → 1.0
    2. Token-set ratio (handles word-order variation)
    3. Levenshtein ratio (handles transliteration variation)
    Returns max(token_set_ratio, levenshtein_ratio).
    """
    q = _normalise_name(query)
    c = _normalise_name(candidate)

    if not q or not c:
        return 0.0

    if q == c:
        return 1.0

    # Token-set similarity
    q_tokens = frozenset(q.split())
    c_tokens = frozenset(c.split())
    intersection = len(q_tokens & c_tokens)
    union = len(q_tokens | c_tokens)
    token_set_ratio = intersection / union if union else 0.0

    # Levenshtein ratio
    lev_ratio = _levenshtein_ratio(q, c)

    # Partial token match: check if all query tokens appear in candidate
    partial_score = 0.0
    if q_tokens and q_tokens.issubset(c_tokens):
        partial_score = 0.95  # all query tokens found in candidate

    return max(token_set_ratio, lev_ratio, partial_score)


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """
    Compute Levenshtein similarity ratio.
    Uses rapidfuzz if available (C extension, ~10x faster),
    falls back to pure-Python implementation.
    """
    try:
        from rapidfuzz.distance import Levenshtein  # type: ignore

        distance = Levenshtein.distance(s1, s2)
        max_len = max(len(s1), len(s2))
        return 1.0 - (distance / max_len) if max_len else 1.0
    except ImportError:
        pass

    # Pure-Python Levenshtein
    return _pure_levenshtein_ratio(s1, s2)


def _pure_levenshtein_ratio(s1: str, s2: str) -> float:
    """Wagner-Fischer algorithm."""
    m, n = len(s1), len(s2)
    if m == 0 or n == 0:
        return 0.0

    # Use two-row DP to save memory
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, prev

    distance = prev[n]
    max_len = max(m, n)
    return 1.0 - (distance / max_len)


# ---------------------------------------------------------------------------
# OFAC SDN XML parser
# ---------------------------------------------------------------------------

# Advanced SDN XML namespaces
_NS = {
    "sdn": "http://tempuri.org/sdnList.xsd",
}

# Fallback for older/simple SDN format (no namespace)
_NS_EMPTY: dict[str, str] = {}


def _parse_sdn_xml(xml_bytes: bytes) -> list[SDNEntry]:
    """
    Parse OFAC Advanced SDN XML into a list of SDNEntry objects.
    Handles both the namespaced advanced format and the legacy flat format.
    """
    entries: list[SDNEntry] = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.error("Failed to parse SDN XML: %s", exc)
        return entries

    # Detect namespace
    tag = root.tag
    ns = ""
    if tag.startswith("{"):
        ns = tag[1:tag.index("}")]

    def _tag(local: str) -> str:
        return f"{{{ns}}}{local}" if ns else local

    for sdnEntry in root.findall(_tag("sdnEntry")):
        uid = sdnEntry.findtext(_tag("uid")) or ""
        sdn_type = sdnEntry.findtext(_tag("sdnType")) or "INDIVIDUAL"
        program_el = sdnEntry.find(_tag("programList"))

        programs: list[str] = []
        if program_el is not None:
            for prog in program_el.findall(_tag("program")):
                programs.append(prog.text or "")

        remarks = sdnEntry.findtext(_tag("remarks")) or ""

        # Names: lastName + firstName + akaList
        names: list[str] = []
        last_name = sdnEntry.findtext(_tag("lastName")) or ""
        first_name = sdnEntry.findtext(_tag("firstName")) or ""

        if last_name and first_name:
            names.append(f"{first_name} {last_name}")
            names.append(f"{last_name} {first_name}")
        elif last_name:
            names.append(last_name)
        elif first_name:
            names.append(first_name)

        aka_list = sdnEntry.find(_tag("akaList"))
        if aka_list is not None:
            for aka in aka_list.findall(_tag("aka")):
                aka_last = aka.findtext(_tag("lastName")) or ""
                aka_first = aka.findtext(_tag("firstName")) or ""
                if aka_last and aka_first:
                    names.append(f"{aka_first} {aka_last}")
                    names.append(f"{aka_last} {aka_first}")
                elif aka_last:
                    names.append(aka_last)
                elif aka_first:
                    names.append(aka_first)

        # DOB
        dob_strings: list[str] = []
        id_list = sdnEntry.find(_tag("idList"))
        if id_list is not None:
            for id_doc in id_list.findall(_tag("id")):
                id_type = id_doc.findtext(_tag("idType")) or ""
                if "DOB" in id_type.upper() or "BIRTH" in id_type.upper():
                    dob_val = id_doc.findtext(_tag("idNumber")) or ""
                    if dob_val:
                        dob_strings.append(dob_val)

        # Nationality
        nationalities: list[str] = []
        nationality_list = sdnEntry.find(_tag("nationalityList"))
        if nationality_list is not None:
            for nat in nationality_list.findall(_tag("nationality")):
                country_code = nat.findtext(_tag("country")) or ""
                if country_code:
                    nationalities.append(country_code.upper()[:2])

        if not names:
            continue

        entries.append(SDNEntry(
            uid=uid,
            sdn_type=sdn_type,
            program=", ".join(programs),
            names=list(dict.fromkeys(names)),  # deduplicate, preserve order
            dob_strings=dob_strings,
            nationalities=nationalities,
            remarks=remarks,
        ))

    logger.info("Parsed %d SDN entries from XML", len(entries))
    return entries


# ---------------------------------------------------------------------------
# S3-backed sanctions checker
# ---------------------------------------------------------------------------

class SanctionsChecker:
    """
    Downloads and caches the OFAC SDN list from S3.

    Thread-safe for Lambda concurrent invocations via a module-level Lock.
    The parsed index is kept in memory between warm invocations.

    Usage:
        checker = SanctionsChecker(bucket="igaming-sanctions", key="ofac/sdn_advanced.xml")
        checker.ensure_loaded()
        matches = checker.search(name="John Smith", dob="1980-01-15")
    """

    def __init__(
        self,
        bucket: str,
        key: str,
        region: str = "us-east-1",
        refresh_interval_hours: int = 24,
    ) -> None:
        self._bucket = bucket
        self._key = key
        self._region = region
        self._refresh_interval = refresh_interval_hours * 3600
        self._s3 = boto3.client("s3", region_name=region)
        self._entries: list[SDNEntry] = []
        self._loaded_at: float = 0.0
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Loading / refreshing
    # ------------------------------------------------------------------

    def ensure_loaded(self) -> None:
        """Load SDN data if not yet loaded or if refresh interval has elapsed."""
        now = time.time()
        if self._entries and (now - self._loaded_at) < self._refresh_interval:
            return
        with self._lock:
            # Double-check after acquiring lock
            if self._entries and (now - self._loaded_at) < self._refresh_interval:
                return
            self._load_from_s3()

    def _load_from_s3(self) -> None:
        """Download and parse the SDN XML from S3."""
        logger.info("Loading SDN list from s3://%s/%s", self._bucket, self._key)
        t0 = time.perf_counter()
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=self._key)
            xml_bytes = resp["Body"].read()
            entries = _parse_sdn_xml(xml_bytes)
            if entries:
                self._entries = entries
                self._loaded_at = time.time()
                elapsed = (time.perf_counter() - t0) * 1000
                logger.info(
                    "SDN list loaded: %d entries, %.1fms, size=%d bytes",
                    len(entries), elapsed, len(xml_bytes),
                )
            else:
                logger.warning("SDN XML parsed to 0 entries — keeping previous index")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("NoSuchKey", "NoSuchBucket"):
                logger.warning(
                    "SDN file not found at s3://%s/%s. Run refresh() to populate.",
                    self._bucket, self._key,
                )
            else:
                logger.error("S3 error loading SDN: %s", exc)

    def refresh(self, download_url: str = OFAC_SDN_URL) -> bool:
        """
        Download the latest OFAC SDN list from treasury.gov and upload to S3.
        Should be called by a scheduled Lambda (e.g. EventBridge cron).
        Returns True on success.
        """
        import urllib.request

        logger.info("Refreshing SDN list from %s", download_url)
        try:
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "igaming-compliance-bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                xml_bytes = resp.read()

            self._s3.put_object(
                Bucket=self._bucket,
                Key=self._key,
                Body=xml_bytes,
                ContentType="application/xml",
                Metadata={
                    "source": download_url,
                    "refreshed_at": str(int(time.time())),
                    "size_bytes": str(len(xml_bytes)),
                },
            )
            logger.info(
                "SDN list uploaded to s3://%s/%s (%d bytes)",
                self._bucket, self._key, len(xml_bytes),
            )

            # Invalidate in-memory cache
            self._loaded_at = 0.0
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error("SDN refresh failed: %s", exc, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        name: str,
        dob: str = "",
        nationality: str = "",
        min_score: float = LEVENSHTEIN_THRESHOLD,
        max_results: int = MAX_RESULTS,
    ) -> list[dict[str, Any]]:
        """
        Fuzzy-search the loaded SDN index for a given name.

        Args:
            name:         Full name to search.
            dob:          Date of birth string (any format) for corroboration.
            nationality:  2-letter country code for corroboration.
            min_score:    Minimum similarity score (0-1) to include in results.
            max_results:  Maximum number of results to return.

        Returns:
            List of match dicts sorted by score descending:
            [{"uid": ..., "matched_name": ..., "score": ..., "list_type": ..., ...}]
        """
        self.ensure_loaded()

        if not name or not self._entries:
            return []

        normalised_query = _normalise_name(name)
        if not normalised_query:
            return []

        matches: list[SanctionsMatch] = []

        for entry in self._entries:
            best_name_score = 0.0
            best_name = ""

            for candidate_name in entry.names:
                score = _compute_similarity(normalised_query, candidate_name)
                if score > best_name_score:
                    best_name_score = score
                    best_name = candidate_name

            if best_name_score < min_score:
                continue

            # DOB corroboration
            dob_matched = False
            if dob and entry.dob_strings:
                normalised_dob = re.sub(r"[^0-9]", "", dob)
                for entry_dob in entry.dob_strings:
                    entry_dob_digits = re.sub(r"[^0-9]", "", entry_dob)
                    if normalised_dob and normalised_dob in entry_dob_digits:
                        dob_matched = True
                        break
                    if entry_dob_digits and entry_dob_digits in normalised_dob:
                        dob_matched = True
                        break

            # Nationality corroboration
            nat_matched = False
            if nationality and entry.nationalities:
                nat_matched = nationality.upper()[:2] in entry.nationalities

            final_score = best_name_score
            if dob_matched:
                final_score = min(1.0, final_score + DOB_CORROBORATION_BONUS)
            if nat_matched:
                final_score = min(1.0, final_score + 0.02)

            matches.append(SanctionsMatch(
                uid=entry.uid,
                matched_name=best_name,
                query_name=name,
                score=final_score,
                sdn_type=entry.sdn_type,
                program=entry.program,
                list_type=entry.list_type,
                dob_matched=dob_matched,
                nationality_matched=nat_matched,
            ))

        # Sort by score descending, then return top N
        matches.sort(key=lambda m: m.score, reverse=True)
        top = matches[:max_results]

        return [
            {
                "uid": m.uid,
                "matched_name": m.matched_name,
                "query_name": m.query_name,
                "score": round(m.score, 4),
                "sdn_type": m.sdn_type,
                "program": m.program,
                "list_type": m.list_type,
                "dob_matched": m.dob_matched,
                "nationality_matched": m.nationality_matched,
            }
            for m in top
        ]

    # ------------------------------------------------------------------
    # Stats / introspection
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return runtime statistics about the loaded index."""
        return {
            "entries_loaded": len(self._entries),
            "loaded_at": self._loaded_at,
            "age_seconds": int(time.time() - self._loaded_at) if self._loaded_at else None,
            "refresh_interval_hours": self._refresh_interval / 3600,
            "bucket": self._bucket,
            "key": self._key,
        }

    def entry_count(self) -> int:
        self.ensure_loaded()
        return len(self._entries)


# ---------------------------------------------------------------------------
# Standalone refresh Lambda handler
# ---------------------------------------------------------------------------

def refresh_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Separate Lambda handler for the scheduled SDN list refresh.
    Triggered by EventBridge cron: rate(1 day)

    Environment variables:
      SDN_BUCKET, SDN_KEY, AWS_REGION
    """
    bucket = os.environ.get("SDN_BUCKET", "igaming-sanctions")
    key = os.environ.get("SDN_KEY", "ofac/sdn_advanced.xml")
    region = os.environ.get("AWS_REGION", "us-east-1")

    checker = SanctionsChecker(bucket=bucket, key=key, region=region)
    success = checker.refresh()

    result = {
        "success": success,
        "timestamp": int(time.time()),
        "bucket": bucket,
        "key": key,
    }

    if not success:
        logger.error("SDN refresh job failed: %s", result)
        raise RuntimeError("SDN list refresh failed")

    logger.info("SDN refresh completed: %s", result)
    return result
