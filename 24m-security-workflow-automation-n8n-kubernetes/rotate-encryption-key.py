#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24m, Security Workflow Automation with n8n on Kubernetes.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Rotate the n8n credentials encryption key.

Companion script for Chapter 24m, section on encryption-key rotation. Run
inside a maintenance window with all workflows idle. It:

  1. Fetches every credential via the n8n REST API (needs an admin API key).
  2. Decrypts each credential's data with the OLD key.
  3. Re-encrypts it with the NEW key.
  4. Writes the re-encrypted value back to the n8n database.

n8n encrypts credential data with AES-256-CBC using a key derived from the
`N8N_ENCRYPTION_KEY` via PBKDF2 (the CryptoJS "EVP" scheme). This mirrors that
scheme so re-encrypted values are readable by n8n after the key swap.

Environment:
  N8N_API_URL     e.g. https://n8n.internal.example/api/v1
  N8N_API_KEY     admin API key
  OLD_KEY, NEW_KEY  the current and replacement encryption keys
                    (source them from OpenBao: secret/n8n/rotation)
  ROTATION_CHECKPOINT_FILE  optional path for the resume checkpoint
                    (default: n8n-key-rotation-checkpoint.json)
  ROTATION_BACKUP_FILE      optional path for the pre-rotation ciphertext
                    backup (default: n8n-credential-backup-<timestamp>.jsonl)

Safety: a mid-run failure followed by a rerun must not corrupt credentials
that were already rotated. Before touching each credential this script:
  - backs up its original (OLD_KEY) ciphertext to ROTATION_BACKUP_FILE
  - checks that OLD_KEY-decryption yields valid JSON before re-encrypting;
    a credential already rotated by a prior partial run will not decrypt
    sanely under OLD_KEY, so it is skipped instead of being mangled
  - records each rotated credential ID in ROTATION_CHECKPOINT_FILE so a
    rerun resumes instead of redoing (and re-touching) finished work
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time

import requests
from Crypto.Cipher import AES  # pycryptodome

API_URL = os.environ["N8N_API_URL"].rstrip("/")
API_KEY = os.environ["N8N_API_KEY"]
OLD_KEY = os.environ["OLD_KEY"].encode()
NEW_KEY = os.environ["NEW_KEY"].encode()

CHECKPOINT_FILE = os.environ.get(
    "ROTATION_CHECKPOINT_FILE", "n8n-key-rotation-checkpoint.json"
)
BACKUP_FILE = os.environ.get(
    "ROTATION_BACKUP_FILE",
    f"n8n-credential-backup-{time.strftime('%Y%m%dT%H%M%S')}.jsonl",
)

SESSION = requests.Session()
SESSION.headers["X-N8N-API-KEY"] = API_KEY


def _evp_bytes_to_key(passphrase: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """CryptoJS-compatible key+IV derivation (MD5, matching n8n's cipher)."""
    derived = b""
    prev = b""
    while len(derived) < 48:  # 32-byte key + 16-byte IV
        prev = hashlib.md5(prev + passphrase + salt).digest()
        derived += prev
    return derived[:32], derived[32:48]


def _decrypt(ciphertext_b64: str, key: bytes) -> bytes:
    raw = base64.b64decode(ciphertext_b64)
    assert raw[:8] == b"Salted__", "unexpected ciphertext format"
    salt = raw[8:16]
    aes_key, iv = _evp_bytes_to_key(key, salt)
    plain = AES.new(aes_key, AES.MODE_CBC, iv).decrypt(raw[16:])
    return plain[: -plain[-1]]  # strip PKCS#7 padding


def _encrypt(plaintext: bytes, key: bytes) -> str:
    salt = os.urandom(8)
    aes_key, iv = _evp_bytes_to_key(key, salt)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    body = AES.new(aes_key, AES.MODE_CBC, iv).encrypt(padded)
    return base64.b64encode(b"Salted__" + salt + body).decode()


def _load_checkpoint() -> set[str]:
    """IDs already rotated by a previous run of this script, if any."""
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    with open(CHECKPOINT_FILE, encoding="utf-8") as f:
        try:
            return set(json.load(f).get("rotated_ids", []))
        except (json.JSONDecodeError, AttributeError):
            return set()


def _save_checkpoint(rotated_ids: set[str]) -> None:
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"rotated_ids": sorted(rotated_ids)}, f)


def _backup_credential(cid: str, name: str, encrypted: str) -> None:
    """Append the pre-rotation (OLD_KEY) ciphertext before touching it."""
    with open(BACKUP_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": cid, "name": name, "data": encrypted}) + "\n")


def _decrypts_to_valid_json(plaintext: bytes) -> bool:
    """Sanity-check that OLD_KEY-decryption produced real credential data.

    n8n credential payloads are JSON objects. If decrypting under OLD_KEY does
    not yield valid JSON, the credential was most likely already re-encrypted
    with NEW_KEY by an earlier, interrupted run (or is corrupt) - either way it
    must not be blindly re-encrypted again, which would turn it into garbage.
    """
    try:
        json.loads(plaintext.decode("utf-8"))
        return True
    except (UnicodeDecodeError, ValueError):
        return False


def rotate() -> int:
    resp = SESSION.get(f"{API_URL}/credentials")
    resp.raise_for_status()
    credentials = resp.json().get("data", [])

    already_rotated = _load_checkpoint()
    rotated_ids = set(already_rotated)
    rotated = 0
    skipped = 0

    for cred in credentials:
        cid = str(cred["id"])
        name = cred.get("name", "?")

        if cid in already_rotated:
            print(f"skipping credential {cid} ({name}) - already rotated per checkpoint")
            skipped += 1
            continue

        detail = SESSION.get(f"{API_URL}/credentials/{cid}").json()
        encrypted = detail.get("data")
        if not encrypted:
            continue

        try:
            plaintext = _decrypt(encrypted, OLD_KEY)
        except Exception as exc:  # noqa: BLE001 - malformed ciphertext, not fatal
            print(f"skipping credential {cid} ({name}) - could not decrypt with OLD_KEY: {exc}")
            skipped += 1
            continue

        if not _decrypts_to_valid_json(plaintext):
            print(
                f"skipping credential {cid} ({name}) - OLD_KEY decryption is not valid JSON; "
                "likely already rotated or corrupt, not re-encrypting"
            )
            skipped += 1
            continue

        _backup_credential(cid, name, encrypted)

        reencrypted = _encrypt(plaintext, NEW_KEY)
        SESSION.patch(
            f"{API_URL}/credentials/{cid}", json={"data": reencrypted}
        ).raise_for_status()

        rotated_ids.add(cid)
        _save_checkpoint(rotated_ids)
        rotated += 1
        print(f"rotated credential {cid} ({name})")

    print(json.dumps({"rotated": rotated, "skipped": skipped, "total": len(credentials)}))
    return 0


if __name__ == "__main__":
    sys.exit(rotate())
