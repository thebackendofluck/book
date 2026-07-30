# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
global_id.py — Cross-brand identity matching and flag propagation service.

Mirrors GlobalIdController.scala, GlobalIdDAO.scala, FlagType.scala,
MatchRules.scala, FlagFilters.scala, and PropagationRules.scala.

Architecture:
  - FastAPI HTTP service exposing three endpoints:
      POST /register   — cross-check new user, link to existing GID or create new one
      POST /flags      — set a flag and propagate to all linked accounts
      GET  /linked/{id} — list all accounts sharing a Global ID

  - MatchRules: SQL-level identity resolution (SSN, name+DOB+postcode, phone, email)
  - PropagationRules: policy engine determining which linked accounts receive a flag
    All rules are AND-composed: a flag propagates only if every rule returns True.

  - FlagType enum: 25+ flag types covering KYC, self-exclusion, RG interventions,
    marketing blocks, and PEP/sanctions flags.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

import psycopg2
import psycopg2.extras
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Flag types (mirrors FlagType.scala)
# ---------------------------------------------------------------------------

class FlagType(str, Enum):
    """
    All responsible gaming and compliance flags.

    Registration-blocking flags (value >= 101) have higher priority for sorting.
    Single-account flags do NOT propagate across brands.
    """
    # KYC and identity
    PEP                 = "pep"
    SANCTION            = "sanction"
    AGE_VERIFIED        = "age_verified"
    ID_CHECK_FAILED     = "id_check_failed"
    KYC_PENDING_FLAG    = "kyc_pending_flag"
    KYC_LOCK_FLAG       = "kyc_lock_flag"
    KYC_DOCS_REQUIRED   = "kyc_docs_required"
    KYC_ID_REQUIRED     = "kyc_id_required"
    KYC_ADDRESS_REQUIRED = "kyc_address_required"

    # Self-exclusion and account restrictions
    NATIONAL_EXCLUSION        = "national-exclusion"
    GLOBAL_SELF_EXCLUSION     = "global_self_exclusion"
    SELF_EXCLUSION_FLAG       = "self_exclusion_flag"
    OPERATOR_EXCLUSION        = "operator_exclusion"
    PROMO_BAN_FLAG            = "promo_ban_flag"
    TEMP_LOCK_FLAG            = "temp_lock_flag"
    FULL_BLOCK_FLAG           = "full_block_flag"
    BONUS_BLOCK_FLAG          = "bonus_block_flag"
    SINGLE_ACCOUNT_BLOCK      = "single_account_block"      # never propagates
    TIMED_LOCK_FLAG           = "timed_lock_flag"
    NETWORK_BONUS_FLAG        = "network_bonus_flag"
    LICENSEE_REGISTRATION_BLOCK = "licensee_registration_block"

    # Marketing
    MARKETING_BLOCK_FLAG    = "marketing_block_flag"
    GLOBAL_MARKETING_EXCLUDE = "global_marketing_exclude"

    # Responsible gaming interventions
    RG1_FLAG      = "rg1"
    RG2_FLAG      = "rg2"
    RG3_FLAG      = "rg3"
    RG3_LOCK_FLAG = "rg3_lock_flag"


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class UserRegistrationRequest(BaseModel):
    user_id:       int
    brand_id:      int
    email:         Optional[str]  = None
    first_name:    str
    last_name:     str
    postal_code:   str
    date_of_birth: date
    phone:         Optional[str]  = None
    country:       Optional[str]  = None
    ssn:           Optional[str]  = None
    ip:            Optional[str]  = None
    cookie_value:  Optional[str]  = None

    model_config = {"from_attributes": True}


class SetFlagRequest(BaseModel):
    user_id:    int
    brand_id:   int
    flag_type:  FlagType
    flag_value: bool
    comment:    Optional[str] = None

    model_config = {"from_attributes": True}


class LinkedUserResponse(BaseModel):
    user_id:     int
    brand_id:    int
    email:       Optional[str]
    first_name:  str
    last_name:   str
    postal_code: str
    dob:         date
    phone:       Optional[str]
    country:     Optional[str]
    ssn:         Optional[str]
    enabled:     bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Domain types used internally
# ---------------------------------------------------------------------------

class UserFlag:
    def __init__(
        self,
        global_id:        int,
        flag_type:        FlagType,
        flag_value:       bool,
        original_user_id: Optional[int] = None,
        set_on:           Optional[datetime] = None,
    ) -> None:
        self.global_id        = global_id
        self.flag_type        = flag_type
        self.flag_value       = flag_value
        self.original_user_id = original_user_id
        self.set_on           = set_on or datetime.now(timezone.utc)


class FindUsersByGidResult:
    def __init__(self, user_id: int, brand_id: int, country: str,
                 dob: Optional[date] = None, post_code: Optional[str] = None) -> None:
        self.user_id   = user_id
        self.brand_id  = brand_id
        self.country   = country
        self.dob       = dob
        self.post_code = post_code


class UserMatchingDetails:
    def __init__(self, brand_id: int, country: Optional[str],
                 postal_code: str, dob: date) -> None:
        self.brand_id   = brand_id
        self.country    = country
        self.postal_code = postal_code
        self.dob        = dob


class LinkedUserDetails:
    def __init__(self, user_id: int, brand_id: int, country: str,
                 postal_code: str, dob: date) -> None:
        self.user_id    = user_id
        self.brand_id   = brand_id
        self.country    = country
        self.postal_code = postal_code
        self.dob        = dob


# ---------------------------------------------------------------------------
# Match rules — cross-brand identity resolution (mirrors MatchRules.scala)
#
# Four rules in priority order (strongest → weakest):
#   1. SSN + country   (national ID match)
#   2. First-initial + last-name + DOB + postcode  (demographic)
#   3. Phone + DOB + country
#   4. Email
#
# Each match rule is a list of (column, expression, value) triples.
# An AND within a rule, OR across rules.
# ---------------------------------------------------------------------------

def _normalize_name(val: str) -> str:
    """Strip whitespace and non-word chars, lowercase."""
    return re.sub(r"[\s\W]", "", val).lower()


def _normalize_postcode(val: str) -> str:
    return val.replace(" ", "").lower()


def _normalize_phone(val: str) -> str:
    """Compare last 9 digits — handles country code variations."""
    return val.replace(" ", "")[-9:]


def _first_letter(val: str) -> str:
    normalized = _normalize_name(val)
    return normalized[0] if normalized else ""


def cross_check_query(reg: UserRegistrationRequest, exclude_user_id: Optional[int] = None) -> tuple[str, list]:
    """
    Build a parameterised PostgreSQL query that finds all existing users
    whose identity fields match the registration request under any of the
    four match rules.

    Returns (sql_text, params).
    """
    conditions = []
    params: list = []

    # Rule 1: SSN + country
    if reg.ssn and reg.country:
        conditions.append("(BTRIM(ui.ssn) = %s AND ui.country = %s)")
        params += [reg.ssn.strip(), reg.country]

    # Rule 2: first-letter-of-first-name + last-name + DOB + postcode
    if reg.first_name and reg.last_name and reg.date_of_birth and reg.postal_code:
        conditions.append(
            """(
               LEFT(LOWER(REGEXP_REPLACE(ui.first_name, '\\s|\\W', '', 'g')), 1)
                 = LEFT(LOWER(REGEXP_REPLACE(%s,          '\\s|\\W', '', 'g')), 1)
               AND LOWER(REGEXP_REPLACE(ui.last_name, '\\s|\\W', '', 'g'))
                 = LOWER(REGEXP_REPLACE(%s,            '\\s|\\W', '', 'g'))
               AND ui.dob = %s
               AND LOWER(REPLACE(ui.postcode, ' ', '')) = LOWER(REPLACE(%s, ' ', ''))
            )"""
        )
        params += [reg.first_name, reg.last_name, reg.date_of_birth, reg.postal_code]

    # Rule 3: phone + DOB + country
    if reg.phone and reg.date_of_birth and reg.country:
        conditions.append(
            "(RIGHT(REPLACE(ui.phone, ' ', ''), 9) = RIGHT(REPLACE(%s, ' ', ''), 9) "
            "AND ui.dob = %s AND ui.country = %s)"
        )
        params += [reg.phone, reg.date_of_birth, reg.country]

    # Rule 4: email (weakest)
    if reg.email:
        conditions.append("(LOWER(BTRIM(ui.email)) = LOWER(BTRIM(%s)))")
        params += [reg.email]

    if not conditions:
        return "", []

    where = " OR\n".join(conditions)
    exclude_clause = "AND ui.user_id != %s " if exclude_user_id else ""
    if exclude_user_id:
        params = [exclude_user_id] + params

    sql = f"""
        SELECT ui.global_id, ui.user_id
        FROM global_id.user_info ui
        JOIN global_id.users u ON ui.user_id = u.user_id AND u.enabled = TRUE
        WHERE {exclude_clause}({where})
        ORDER BY (CASE
            {'WHEN BTRIM(ui.ssn) = %s AND ui.country = %s THEN 0' if reg.ssn and reg.country else ''}
            ELSE 9999
        END)
    """
    # Append ordering params for SSN rule if applicable
    if reg.ssn and reg.country:
        params += [reg.ssn.strip(), reg.country]

    return sql, params


# ---------------------------------------------------------------------------
# Global ID DAO (mirrors GlobalIdDAO.scala key operations)
# ---------------------------------------------------------------------------

class GlobalIdDAO:
    """
    Core cross-brand identity operations.

    All GID creation is transactional: gid row + user row + user_info row
    are inserted atomically. Cross-check uses priority-ordered SQL match rules.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def find_user_gid(self, user_id: int, brand_id: int) -> Optional[int]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT global_id FROM global_id.users WHERE user_id=%s AND brand_id=%s",
                (user_id, brand_id),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def create_gid(self, reg: UserRegistrationRequest) -> int:
        """Atomically create a new GID + user + user_info rows."""
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO global_id.gid (created_on) VALUES (%s) RETURNING id",
                (datetime.now(timezone.utc),),
            )
            gid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO global_id.users
                   (global_id, user_id, brand_id, created_on, enabled)
                   VALUES (%s,%s,%s,%s,FALSE)""",
                (gid, reg.user_id, reg.brand_id, datetime.now(timezone.utc)),
            )
            self._insert_user_info(cur, gid, reg)
        self._conn.commit()
        return gid

    def cross_check_user(self, reg: UserRegistrationRequest,
                          exclude_user_id: Optional[int] = None) -> list[tuple[int, int]]:
        """Return list of (global_id, user_id) pairs that match via MatchRules."""
        sql, params = cross_check_query(reg, exclude_user_id)
        if not sql:
            return []
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [(r[0], r[1]) for r in cur.fetchall()]

    def add_user(self, global_id: int, reg: UserRegistrationRequest,
                 enabled: bool, matched_gids: Optional[list[int]] = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO global_id.users
                   (global_id, user_id, brand_id, created_on, enabled)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (global_id, reg.user_id, reg.brand_id,
                 datetime.now(timezone.utc), enabled),
            )
            self._insert_user_info(cur, global_id, reg)
        self._conn.commit()

    def is_duplicate(self, gid: int, user_id: int, brand_id: int, dob: date) -> bool:
        """True if another user with same GID + brand + DOB already exists."""
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM global_id.users u
                   LEFT JOIN global_id.user_info ui ON u.user_id = ui.user_id
                   WHERE u.global_id=%s AND u.brand_id=%s
                     AND u.user_id != %s AND u.enabled=TRUE AND ui.dob=%s""",
                (gid, brand_id, user_id, dob),
            )
            count = cur.fetchone()[0]
        return count > 0

    def find_linked_users(self, global_id: int,
                           exclude_user_id: Optional[int] = None) -> list[FindUsersByGidResult]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT u.user_id, u.brand_id, ui.country, ui.dob, ui.postcode
                   FROM global_id.users u
                   LEFT JOIN global_id.user_info ui ON u.user_id = ui.user_id
                   WHERE u.global_id=%s AND u.enabled=TRUE""",
                (global_id,),
            )
            rows = cur.fetchall()
        return [
            FindUsersByGidResult(
                user_id=r["user_id"],
                brand_id=r["brand_id"],
                country=r.get("country") or "",
                dob=r.get("dob"),
                post_code=r.get("postcode"),
            )
            for r in rows
            if not exclude_user_id or r["user_id"] != exclude_user_id
        ]

    def get_linked_users(self, user_id: int) -> list[dict]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT u2.user_id, u2.brand_id, ui2.email,
                          ui2.first_name, ui2.last_name, ui2.postcode,
                          ui2.dob, ui2.phone, ui2.country, ui2.ssn, u2.enabled
                   FROM global_id.users u1
                   JOIN global_id.users u2 ON u1.global_id = u2.global_id
                   LEFT JOIN global_id.user_info ui2 ON u2.user_id = ui2.user_id
                   WHERE u1.user_id=%s""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_user_info(self, user_id: int) -> Optional[dict]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM global_id.user_info WHERE user_id=%s",
                (user_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def _insert_user_info(self, cur: Any, global_id: int, reg: UserRegistrationRequest) -> None:
        cur.execute(
            """INSERT INTO global_id.user_info
               (global_id, user_id, email, first_name, last_name,
                postcode, dob, phone, ip, cookie_value, country, ssn)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (user_id) DO UPDATE SET
                 email=EXCLUDED.email, first_name=EXCLUDED.first_name,
                 last_name=EXCLUDED.last_name, postcode=EXCLUDED.postcode,
                 dob=EXCLUDED.dob, phone=EXCLUDED.phone, country=EXCLUDED.country""",
            (global_id, reg.user_id, reg.email, reg.first_name, reg.last_name,
             reg.postal_code, reg.date_of_birth, reg.phone,
             reg.ip, reg.cookie_value, reg.country, reg.ssn),
        )


# ---------------------------------------------------------------------------
# Flags DAO (minimal: set flag + get all flags for propagation)
# ---------------------------------------------------------------------------

class FlagsDAO:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def set_flag(self, user_id: int, brand_id: int,
                 flag_type: FlagType, flag_value: bool,
                 comment: Optional[str]) -> tuple[UserFlag, list[FindUsersByGidResult]]:
        """Set flag and return (updated flag, linked users for propagation)."""
        with self._conn.cursor() as cur:
            # Upsert the flag
            cur.execute(
                """INSERT INTO global_id.user_flags
                   (user_id, brand_id, flag_type, flag_value, comment, set_on)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, flag_type)
                   DO UPDATE SET flag_value=%s, comment=%s, set_on=%s
                   RETURNING global_id""",
                (user_id, brand_id, flag_type.value, flag_value, comment,
                 datetime.now(timezone.utc),
                 flag_value, comment, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"user {user_id} brand {brand_id} not found in global_id")
            global_id = row[0]
        self._conn.commit()

        uf = UserFlag(global_id=global_id, flag_type=flag_type,
                      flag_value=flag_value, original_user_id=user_id)

        # Return linked users for propagation decision
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT u.user_id, u.brand_id, ui.country, ui.dob, ui.postcode
                   FROM global_id.users u
                   LEFT JOIN global_id.user_info ui ON u.user_id = ui.user_id
                   WHERE u.global_id=%s AND u.enabled=TRUE""",
                (global_id,),
            )
            rows = cur.fetchall()

        linked = [
            FindUsersByGidResult(
                user_id=r["user_id"], brand_id=r["brand_id"],
                country=r.get("country") or "",
                dob=r.get("dob"), post_code=r.get("postcode"),
            )
            for r in rows
        ]
        return uf, linked

    def get_all_flags(self, global_id: int) -> list[UserFlag]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT uf.global_id, uf.flag_type, uf.flag_value,
                          uf.original_user_id, uf.set_on
                   FROM global_id.user_flags uf
                   WHERE uf.global_id=%s""",
                (global_id,),
            )
            rows = cur.fetchall()
        return [
            UserFlag(
                global_id=r["global_id"],
                flag_type=FlagType(r["flag_type"]),
                flag_value=r["flag_value"],
                original_user_id=r.get("original_user_id"),
                set_on=r.get("set_on"),
            )
            for r in rows
        ]

    def get_flag_last_update(self, user_id: int, flag_type: FlagType) -> Optional[datetime]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT set_on FROM global_id.user_flags WHERE user_id=%s AND flag_type=%s",
                (user_id, flag_type.value),
            )
            row = cur.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Propagation rules (mirrors PropagationRules.scala)
#
# A flag propagates to a linked account only if ALL rules return True.
# Rules return True when the rule does not apply to the flag type (pass-through).
# ---------------------------------------------------------------------------

PropagationRule = Callable[[UserFlag, LinkedUserDetails, Any, FlagsDAO], bool]

_GB_FLAGS    = {FlagType.KYC_LOCK_FLAG, FlagType.NATIONAL_EXCLUSION}
_SE_FLAGS    = {FlagType.SELF_EXCLUSION_FLAG, FlagType.MARKETING_BLOCK_FLAG}
_NO_PROP     = {FlagType.SINGLE_ACCOUNT_BLOCK, FlagType.LICENSEE_REGISTRATION_BLOCK}
_KYC_FLAGS   = {FlagType.KYC_LOCK_FLAG, FlagType.KYC_DOCS_REQUIRED,
                FlagType.KYC_ID_REQUIRED, FlagType.KYC_ADDRESS_REQUIRED,
                FlagType.ID_CHECK_FAILED}


def _get_matcher_details(user_id: int, conn: Any) -> Optional[UserMatchingDetails]:
    dao = GlobalIdDAO(conn)
    info = dao.get_user_info(user_id)
    if not info:
        return None
    return UserMatchingDetails(
        brand_id=info.get("brand_id", 0),
        country=info.get("country"),
        postal_code=info.get("postcode", ""),
        dob=info.get("dob", date(1800, 1, 1)),
    )


def gb_propagation_rule(flag: UserFlag, linked: LinkedUserDetails,
                         matcher: Any, flags_dao: FlagsDAO) -> bool:
    """KYC locks and national exclusions only propagate to other GB accounts."""
    if flag.flag_type in _GB_FLAGS:
        if flag.original_user_id and hasattr(matcher, "details"):
            details = matcher.details
            return (details.country or "") == linked.country == "GB"
        return linked.country == "GB"
    return True


def no_propagation_rule(flag: UserFlag, linked: LinkedUserDetails,
                         matcher: Any, flags_dao: FlagsDAO) -> bool:
    """SINGLE_ACCOUNT_BLOCK and LICENSEE_REGISTRATION_BLOCK never propagate."""
    return flag.flag_type not in _NO_PROP


def same_country_propagation_rule(flag: UserFlag, linked: LinkedUserDetails,
                                   matcher: Any, flags_dao: FlagsDAO) -> bool:
    """NETWORK_BONUS_FLAG only propagates within the same country (SGA)."""
    if flag.flag_type == FlagType.NETWORK_BONUS_FLAG:
        if flag.original_user_id and hasattr(matcher, "details"):
            return (matcher.details.country or "") == linked.country
        return True
    return True


def same_brand_or_gb_rule(flag: UserFlag, linked: LinkedUserDetails,
                           matcher: Any, flags_dao: FlagsDAO) -> bool:
    """SELF_EXCLUSION_FLAG and MARKETING_BLOCK_FLAG: same brand globally OR cross-brand within GB."""
    if flag.flag_type in _SE_FLAGS:
        if flag.original_user_id and hasattr(matcher, "details"):
            details = matcher.details
            return (details.brand_id == linked.brand_id or
                    ((details.country or "") == linked.country == "GB"))
        return linked.country == "GB"
    return True


def pep_one_year_rule(flag: UserFlag, linked: LinkedUserDetails,
                       matcher: Any, flags_dao: FlagsDAO) -> bool:
    """PEP flags: require same DOB AND flag set within last year."""
    if flag.flag_type == FlagType.PEP:
        if flag.original_user_id and hasattr(matcher, "details"):
            if matcher.details.dob != linked.dob:
                return False
        last_update = flags_dao.get_flag_last_update(linked.user_id, FlagType.PEP)
        if last_update:
            return last_update + timedelta(days=365) > datetime.now(timezone.utc)
    return True


def kyc_two_year_rule(flag: UserFlag, linked: LinkedUserDetails,
                       matcher: Any, flags_dao: FlagsDAO) -> bool:
    """KYC_PENDING_FLAG: require same DOB + postcode AND flag < 2 years old."""
    if flag.flag_type == FlagType.KYC_PENDING_FLAG:
        if flag.original_user_id and hasattr(matcher, "details"):
            details = matcher.details
            if details.dob != linked.dob:
                return False
            if _normalize_postcode(details.postal_code) != _normalize_postcode(linked.postal_code):
                return False
        last_update = flags_dao.get_flag_last_update(linked.user_id, FlagType.KYC_PENDING_FLAG)
        if last_update:
            return last_update + timedelta(days=730) > datetime.now(timezone.utc)
    return True


def kyc_lock_dob_rule(flag: UserFlag, linked: LinkedUserDetails,
                       matcher: Any, flags_dao: FlagsDAO) -> bool:
    """KYC lock flags require DOB + postcode match before propagating."""
    if flag.flag_type in _KYC_FLAGS:
        if flag.original_user_id and hasattr(matcher, "details"):
            details = matcher.details
            return (details.dob == linked.dob and
                    _normalize_postcode(details.postal_code) == _normalize_postcode(linked.postal_code))
    return True


def kyc_approval_rule(flag: UserFlag, linked: LinkedUserDetails,
                       matcher: Any, flags_dao: FlagsDAO) -> bool:
    """KYC approval (flag_value=False) never propagates."""
    if flag.flag_type in {FlagType.KYC_PENDING_FLAG, FlagType.ID_CHECK_FAILED}:
        return flag.flag_value  # only propagate when setting (True), not clearing
    return True


ALL_PROPAGATION_RULES: list[PropagationRule] = [
    kyc_approval_rule,
    gb_propagation_rule,
    no_propagation_rule,
    same_country_propagation_rule,
    same_brand_or_gb_rule,
    pep_one_year_rule,
    kyc_two_year_rule,
    kyc_lock_dob_rule,
]


class _InlineUserMatcher:
    """Matcher that uses caller-supplied details instead of DB lookup."""
    def __init__(self, details: UserMatchingDetails) -> None:
        self.details = details


# ---------------------------------------------------------------------------
# Flag propagation filter (mirrors FlagFilters.scala)
# ---------------------------------------------------------------------------

def filter_linked_users_for_propagation(
    flag: UserFlag,
    linked_users: list[FindUsersByGidResult],
    source_user_details: Optional[UserMatchingDetails],
    flags_dao: FlagsDAO,
    rules: list[PropagationRule] = ALL_PROPAGATION_RULES,
) -> list[FindUsersByGidResult]:
    """Return only those linked users that ALL propagation rules approve."""
    matcher = _InlineUserMatcher(source_user_details) if source_user_details else None
    result = []
    for u in linked_users:
        linked = LinkedUserDetails(
            user_id=u.user_id,
            brand_id=u.brand_id,
            country=u.country or "",
            postal_code=u.post_code or "dummy",
            dob=u.dob or date(1800, 1, 1),
        )
        if all(rule(flag, linked, matcher, flags_dao) for rule in rules):
            result.append(u)
    return result


# ---------------------------------------------------------------------------
# FastAPI application (mirrors GlobalIdController.scala)
# ---------------------------------------------------------------------------

app = FastAPI(title="Global ID Service", version="1.0.0")


def _get_db():
    db_url = os.environ.get("DATABASE_URL", "")
    conn = psycopg2.connect(db_url)
    try:
        yield conn
    finally:
        conn.close()


@app.post("/register")
def register_user(body: UserRegistrationRequest, conn=Depends(_get_db)):
    """
    Register a new user, linking to an existing Global ID if a match is found.

    Logic:
      1. Already registered → 412 Precondition Failed
      2. No cross-check match → create new GID
      3. Match found + same brand + same DOB → 409 Conflict (duplicate account)
      4. Match found + different brand → link to existing GID, propagate flags
    """
    if body.email is None and body.ssn is None:
        raise HTTPException(status_code=400,
                            detail="email and ssn cannot both be empty")

    gid_dao   = GlobalIdDAO(conn)
    flags_dao = FlagsDAO(conn)

    if gid_dao.find_user_gid(body.user_id, body.brand_id) is not None:
        raise HTTPException(status_code=412,
                            detail=f"User {body.user_id} already registered")

    matches = gid_dao.cross_check_user(body)

    if not matches:
        gid = gid_dao.create_gid(body)
        return {"status": "OK",
                "message": f"User {body.user_id} registered with global id {gid}",
                "gid": gid}

    first_gid, first_uid = matches[0]
    gids = list({m[0] for m in matches})

    gid_dao.add_user(
        global_id=first_gid,
        reg=body,
        enabled=False,
        matched_gids=gids if len(gids) > 1 else [],
    )

    if gid_dao.is_duplicate(first_gid, body.user_id, body.brand_id, body.date_of_birth):
        return JSONResponse(
            status_code=409,
            content={
                "status": "OK",
                "message": f"Matching user already registered with brand {body.brand_id}",
                "gid": first_gid,
            },
        )

    # Propagate flags from all linked accounts to the new user
    linked = gid_dao.find_linked_users(first_gid, exclude_user_id=body.user_id)
    all_flags = flags_dao.get_all_flags(first_gid)
    source_details = UserMatchingDetails(
        brand_id=body.brand_id,
        country=body.country,
        postal_code=body.postal_code,
        dob=body.date_of_birth,
    )

    propagated_flags = []
    for flag in all_flags:
        eligible = filter_linked_users_for_propagation(
            flag, linked, source_details, flags_dao
        )
        if eligible:
            propagated_flags.append({
                "flag_type":  flag.flag_type.value,
                "flag_value": flag.flag_value,
            })

    # Deduplicate: keep earliest original for each flag type
    seen: set[str] = set()
    unique_flags = []
    for f in propagated_flags:
        if f["flag_type"] not in seen:
            seen.add(f["flag_type"])
            unique_flags.append(f)

    return {
        "status":  "OK",
        "message": f"User {body.user_id} associated with global id {first_gid}",
        "flags":   unique_flags,
        "limits":  [],
        "scores":  [],
        "gid":     first_gid,
    }


@app.post("/flags")
def set_user_flag(body: SetFlagRequest, conn=Depends(_get_db)):
    """
    Set a flag on a user and propagate it to all eligible linked accounts.

    Propagation is filtered by ALL_PROPAGATION_RULES (jurisdiction, expiry, etc.).
    """
    gid_dao   = GlobalIdDAO(conn)
    flags_dao = FlagsDAO(conn)

    try:
        uf, linked_users = flags_dao.set_flag(
            body.user_id, body.brand_id, body.flag_type, body.flag_value, body.comment
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    user_info = gid_dao.get_user_info(body.user_id)
    source_details: Optional[UserMatchingDetails] = None
    if user_info:
        source_details = UserMatchingDetails(
            brand_id=body.brand_id,
            country=user_info.get("country"),
            postal_code=user_info.get("postcode", ""),
            dob=user_info.get("dob", date(1800, 1, 1)),
        )

    targets = filter_linked_users_for_propagation(uf, linked_users, source_details, flags_dao)
    others  = [u for u in targets if u.user_id != body.user_id]

    log.info("flag propagation", flag_type=body.flag_type.value,
             source_user=body.user_id, propagated_to=len(others))

    return {
        "status":   "OK",
        "message":  f"Flag {body.flag_type.value} updated to {body.flag_value}",
        "propagated_to": [u.user_id for u in others],
    }


@app.get("/linked/{user_id}")
def get_linked_users(user_id: int, conn=Depends(_get_db)):
    """Return all accounts sharing the same Global ID as the given user."""
    gid_dao = GlobalIdDAO(conn)
    all_users = gid_dao.get_linked_users(user_id)
    others = [u for u in all_users if u["user_id"] != user_id]
    if not all_users:
        raise HTTPException(status_code=404,
                            detail=f"user {user_id} not found")
    return others


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )
    uvicorn.run(
        "global_id:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
    )
