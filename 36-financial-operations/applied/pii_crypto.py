# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""T38 — FastAPI PII decrypt/encrypt middleware PoC.

Minimal PoC bound to one endpoint (`GET /players/{id}/profile`). Key comes
from OpenBao Transit (`casino/transit/keys/pii`) via AppRole, cached in
memory with short TTL. Decryption happens in a dependency, not globally.

Deliberate scope:
    * One endpoint only — proving the pattern, not rolling it across the app.
    * Symmetric pgp_sym_encrypt/decrypt; Transit used as a KEK to derive the
      session DEK (envelope encryption).
    * No silent fallback: if OpenBao is unreachable the dependency raises 503.

Follow-ups tracked in security/s3-s6/pgcrypto/README.md:
    * Derive DEK per-tenant (not global) once multi-tenancy lands.
    * Move to pgp_pub_encrypt with per-workload keypairs.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

OPENBAO_ADDR = os.environ["BAO_ADDR"]
OPENBAO_ROLE_ID = os.environ["BAO_APPROLE_ROLE_ID"]
OPENBAO_SECRET_ID = os.environ["BAO_APPROLE_SECRET_ID"]
TRANSIT_KEY = os.environ.get("PII_TRANSIT_KEY", "casino/transit/keys/pii")
DEK_CACHE_TTL_S = 300


@dataclass
class _CachedDEK:
    value: str
    expires_at: float


_dek_cache: _CachedDEK | None = None


def _approle_login() -> str:
    r = httpx.post(
        f"{OPENBAO_ADDR}/v1/auth/approle/login",
        json={"role_id": OPENBAO_ROLE_ID, "secret_id": OPENBAO_SECRET_ID},
        timeout=5.0,
    )
    r.raise_for_status()
    return r.json()["auth"]["client_token"]


def _fetch_dek() -> str:
    """Generate a data-encryption-key via Transit (envelope pattern)."""
    token = _approle_login()
    r = httpx.post(
        f"{OPENBAO_ADDR}/v1/{TRANSIT_KEY}/datakey/plaintext",
        headers={"X-Vault-Token": token},
        timeout=5.0,
    )
    r.raise_for_status()
    return r.json()["data"]["plaintext"]


def get_pii_key() -> str:
    global _dek_cache
    now = time.monotonic()
    if _dek_cache and _dek_cache.expires_at > now:
        return _dek_cache.value
    try:
        dek = _fetch_dek()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail="PII key service unavailable — refusing to serve PII endpoint",
        ) from e
    _dek_cache = _CachedDEK(value=dek, expires_at=now + DEK_CACHE_TTL_S)
    return dek


def decrypt_player_profile(
    player_id: int,
    db: Session = Depends(get_db),
    key: str = Depends(get_pii_key),
) -> dict:
    row = db.execute(
        text(
            """
            SELECT id,
                   pgp_sym_decrypt(email::bytea,       :k) AS email,
                   pgp_sym_decrypt(cpf::bytea,         :k) AS cpf,
                   pgp_sym_decrypt(phone::bytea,       :k) AS phone,
                   pgp_sym_decrypt(dob::bytea,         :k) AS dob,
                   pgp_sym_decrypt(kyc_doc_number::bytea, :k) AS kyc_doc_number
              FROM players
             WHERE id = :pid
            """
        ),
        {"k": key, "pid": player_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="player not found")
    return dict(row)
