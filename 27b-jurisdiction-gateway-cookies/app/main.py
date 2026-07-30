# Companion code for "The Backend of Luck" - Chapter 27b, The Jurisdiction Transfer Gateway and Cookie Consent.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Jurisdiction Transfer Gateway.

Decides whether a data transfer (from_jurisdiction → to_destination, for a given
data_class and session) is legally permitted, and records the decision for
audit. Rules live in YAML (/etc/jurisdiction-gateway/rules.yaml), mirrored
into Redis on startup/reload for O(1) evaluation. SQLite stores the
append-only decision log for regulator evidence (Postgres is a later ticket —
schema is portable).

Design choices (matter for regulator review):
  * Fail-safe-DENY: if Redis is unreachable or a rule is missing, the evaluator
    returns allowed=false. A missing rule is never interpreted as permission.
  * Session-scoped caching: repeated evaluations for the same
    (session_id, from, to, data_class) tuple return the same decision_id for
    the session's lifetime (default 30 min), so a player's audit trail shows
    one coherent decision instead of N stochastic ones.
  * Versioned YAML: every reload hashes the file (sha256); rule-set
    provenance is traceable.
  * Decisions are immutable; the audit log is append-only JSONL + SQLite.
  * Runs on K3s as a Deployment (ConfigMap-backed rules, Secret for admin
    token). Locally a systemd unit also works.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import redis
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

LOG_DIR = Path(os.environ.get("JGW_LOG_DIR", "/var/log/jurisdiction-gateway"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"

RULES_PATH = Path(os.environ.get("JGW_RULES", "/etc/jurisdiction-gateway/rules.yaml"))
REDIS_URL = os.environ.get("JGW_REDIS_URL", "redis://127.0.0.1:6379/3")
SQLITE_PATH = Path(os.environ.get("JGW_SQLITE", "/var/lib/jurisdiction-gateway/audit.db"))
SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
SESSION_TTL = int(os.environ.get("JGW_SESSION_TTL", "1800"))
PORT = int(os.environ.get("JGW_PORT", "8210"))
ADMIN_TOKEN = os.environ.get("JGW_ADMIN_TOKEN", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s jgw %(levelname)s %(message)s")
log = logging.getLogger("jgw")


# ---------- Rule schema ----------

class Rule(BaseModel):
    from_jurisdiction: str
    to_destination: str
    data_class: str
    mechanism: str
    effective_from: str | None = None
    expires_at: str | None = None
    review_by: str | None = None
    citation: str = ""
    notes: str = ""
    allowed: bool = True
    tia_required: bool = False


class EvalRequest(BaseModel):
    from_jurisdiction: str = Field(..., examples=["EU-DE"])
    to_destination: str = Field(..., examples=["Cloudflare-US"])
    data_class: str = Field(..., examples=["PII"])
    session_id: str | None = None
    player_hash: str | None = None


class EvalResponse(BaseModel):
    decision_id: str
    allowed: bool
    mechanism: str
    reasoning: str
    expires_at: str | None = None
    review_by: str | None = None
    tia_required: bool = False
    rules_sha256: str
    cached: bool = False


# ---------- Redis ----------

_r: redis.Redis | None = None
_loaded_sha: str = ""
_loaded_at_iso: str = ""


def redis_client() -> redis.Redis:
    global _r
    if _r is None:
        _r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _r


def set_redis(client: redis.Redis) -> None:
    """Test hook to inject a fake Redis."""
    global _r
    _r = client


# ---------- SQLite audit ----------

@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    conn = sqlite3.connect(SQLITE_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn.cursor()
    finally:
        conn.close()


def init_db() -> None:
    """Create audit schema if absent. Idempotent."""
    with db_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jgw_decisions (
              decision_id   TEXT PRIMARY KEY,
              ts            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
              from_jur      TEXT NOT NULL,
              to_dest       TEXT NOT NULL,
              data_class    TEXT NOT NULL,
              session_id    TEXT,
              player_hash   TEXT,
              allowed       INTEGER NOT NULL,
              mechanism     TEXT NOT NULL,
              reasoning     TEXT NOT NULL,
              rules_sha256  TEXT NOT NULL,
              expires_at    TEXT,
              cached        INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS jgw_ts_idx ON jgw_decisions(ts DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS jgw_from_to_idx ON jgw_decisions(from_jur, to_dest)")
        cur.execute("CREATE INDEX IF NOT EXISTS jgw_session_idx ON jgw_decisions(session_id) WHERE session_id IS NOT NULL")


# ---------- Rule loading ----------

def rule_key(frm: str, to: str, cls: str) -> str:
    return f"jgw:rule:{frm.upper()}:{to}:{cls.upper()}"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    global _loaded_sha, _loaded_at_iso
    p = path or RULES_PATH
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw) or {}
    rules_list = data.get("rules", [])

    r = redis_client()
    for k in list(r.scan_iter("jgw:rule:*")):
        r.delete(k)
    validated = []
    for raw_rule in rules_list:
        rule = Rule(**raw_rule)
        r.set(rule_key(rule.from_jurisdiction, rule.to_destination, rule.data_class),
              rule.model_dump_json())
        validated.append(rule.model_dump())
    r.set("jgw:meta:sha256", sha)
    r.set("jgw:meta:loaded_at", datetime.now(timezone.utc).isoformat())
    r.set("jgw:meta:count", str(len(validated)))
    _loaded_sha = sha
    _loaded_at_iso = datetime.now(timezone.utc).isoformat()
    log.info("rules loaded: %d rules, sha=%s", len(validated), sha[:12])
    return {"count": len(validated), "sha256": sha, "loaded_at": _loaded_at_iso}


def lookup_rule(frm: str, to: str, cls: str) -> Rule | None:
    r = redis_client()
    raw = r.get(rule_key(frm, to, cls))
    if not raw:
        return None
    return Rule.model_validate_json(raw)


# ---------- Evaluation ----------

def session_cache_key(session_id: str, frm: str, to: str, cls: str) -> str:
    return f"jgw:sess:{session_id}:{frm.upper()}:{to}:{cls.upper()}"


def _is_expired(iso: str | None) -> bool:
    if not iso:
        return False
    try:
        exp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= exp


def evaluate(req: EvalRequest) -> EvalResponse:
    r = redis_client()

    # 1. session cache
    if req.session_id:
        cached_raw = r.get(session_cache_key(req.session_id, req.from_jurisdiction,
                                              req.to_destination, req.data_class))
        if cached_raw:
            cached = json.loads(cached_raw)
            cached["cached"] = True
            return EvalResponse(**cached)

    # 2. rule lookup
    rule = lookup_rule(req.from_jurisdiction, req.to_destination, req.data_class)
    if not rule:
        resp = EvalResponse(
            decision_id=str(uuid.uuid4()),
            allowed=False,
            mechanism="no-rule",
            reasoning=(
                f"No rule matches {req.from_jurisdiction} → {req.to_destination} "
                f"for data_class={req.data_class}. Fail-safe denial per gateway policy."
            ),
            rules_sha256=_loaded_sha,
        )
    else:
        expired = _is_expired(rule.expires_at)
        allowed = rule.allowed and not expired
        bits = [f"Mechanism: {rule.mechanism}.", f"Citation: {rule.citation or '—'}."]
        if rule.tia_required:
            bits.append("Transfer Impact Assessment required (Schrems II).")
        if rule.notes:
            bits.append(rule.notes)
        if expired:
            bits.append(f"Rule EXPIRED at {rule.expires_at}; blocking transfer.")
        resp = EvalResponse(
            decision_id=str(uuid.uuid4()),
            allowed=allowed,
            mechanism=rule.mechanism if not expired else "expired",
            reasoning=" ".join(bits),
            expires_at=rule.expires_at,
            review_by=rule.review_by,
            tia_required=rule.tia_required,
            rules_sha256=_loaded_sha,
        )

    # 3. persist + cache
    record_decision(req, resp)
    if req.session_id:
        r.setex(
            session_cache_key(req.session_id, req.from_jurisdiction, req.to_destination, req.data_class),
            SESSION_TTL,
            resp.model_dump_json(),
        )
    return resp


def record_decision(req: EvalRequest, resp: EvalResponse) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "decision_id": resp.decision_id,
        "from": req.from_jurisdiction,
        "to": req.to_destination,
        "data_class": req.data_class,
        "session_id": req.session_id,
        "player_hash": req.player_hash,
        "allowed": resp.allowed,
        "mechanism": resp.mechanism,
        "reasoning": resp.reasoning,
        "rules_sha256": resp.rules_sha256,
        "cached": resp.cached,
    }
    try:
        with DECISIONS_LOG.open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        log.exception("decision jsonl append failed")
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO jgw_decisions
                  (decision_id, from_jur, to_dest, data_class, session_id, player_hash,
                   allowed, mechanism, reasoning, rules_sha256, expires_at, cached)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (resp.decision_id, req.from_jurisdiction, req.to_destination,
                 req.data_class, req.session_id, req.player_hash,
                 int(resp.allowed), resp.mechanism, resp.reasoning, resp.rules_sha256,
                 resp.expires_at, int(resp.cached)),
            )
    except sqlite3.Error:
        log.exception("sqlite insert failed (non-fatal; jsonl is source of truth)")


# ---------- FastAPI ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_rules()
    def _sighup(_signum, _frame):
        log.info("SIGHUP: reloading rules")
        try: load_rules()
        except Exception:
            log.exception("rule reload failed; keeping previous set")
    signal.signal(signal.SIGHUP, _sighup)
    yield


app = FastAPI(title="Jurisdiction Transfer Gateway", version="1.0.0", lifespan=lifespan)


@app.get("/v1/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/v1/readyz")
def readyz():
    r = redis_client()
    try:
        r.ping()
        count = int(r.get("jgw:meta:count") or 0)
    except redis.RedisError as e:
        raise HTTPException(status_code=503, detail=f"redis: {e}")
    if count == 0:
        raise HTTPException(status_code=503, detail="no rules loaded")
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail=f"sqlite: {e}")
    return {
        "status": "ready",
        "rules_count": count,
        "rules_sha256": _loaded_sha,
        "rules_loaded_at": _loaded_at_iso,
    }


@app.post("/v1/evaluate", response_model=EvalResponse)
def evaluate_endpoint(req: EvalRequest):
    return evaluate(req)


@app.get("/v1/rules")
def list_rules():
    r = redis_client()
    out = []
    for k in r.scan_iter("jgw:rule:*"):
        raw = r.get(k)
        if raw: out.append(json.loads(raw))
    return {"count": len(out), "sha256": _loaded_sha, "loaded_at": _loaded_at_iso, "rules": out}


@app.get("/v1/rules/expiring")
def expiring_rules(within_days: int = 90):
    now = datetime.now(timezone.utc)
    r = redis_client()
    out = []
    for k in r.scan_iter("jgw:rule:*"):
        raw = r.get(k)
        if not raw: continue
        rule = Rule.model_validate_json(raw)
        pivot = rule.review_by or rule.expires_at
        if not pivot: continue
        try:
            d = datetime.fromisoformat(pivot.replace("Z", "+00:00"))
        except ValueError:
            continue
        delta_days = (d - now).days
        if delta_days <= within_days:
            out.append({**rule.model_dump(), "days_until": delta_days})
    out.sort(key=lambda x: x["days_until"])
    return {"within_days": within_days, "count": len(out), "rules": out}


@app.post("/v1/reload")
def reload_endpoint(token: str = ""):
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="bad admin token")
    try:
        return load_rules()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"reload failed: {e}")



# ---------- OpenBao transit crypto proxy ----------

import urllib.request, urllib.error, ssl as _ssl

_BAO_ADDR  = os.environ.get("BAO_ADDR", "https://127.0.0.1:8200")
_BAO_TOKEN = os.environ.get("BAO_TOKEN", "")
_BAO_KEY   = os.environ.get("BAO_TRANSIT_KEY", "dsr-field-cipher")
_BAO_SSL   = _ssl._create_unverified_context()  # ops-host uses self-signed cert on LAN

def _bao_call(path: str, body: dict) -> dict:
    if not _BAO_TOKEN:
        raise HTTPException(status_code=503, detail="BAO_TOKEN not configured")
    req = urllib.request.Request(
        f"{_BAO_ADDR}/v1/{path}",
        data=json.dumps(body).encode(),
        headers={"X-Vault-Token": _BAO_TOKEN, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=4, context=_BAO_SSL) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"bao http {e.code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"bao: {e}")


class CryptoReq(BaseModel):
    plaintext: str | None = None
    ciphertext: str | None = None


@app.post("/v1/crypto/encrypt")
def crypto_encrypt(req: CryptoReq):
    import base64
    if not req.plaintext:
        return {"ciphertext": ""}
    pt_b64 = base64.b64encode(req.plaintext.encode()).decode()
    d = _bao_call(f"transit/encrypt/{_BAO_KEY}", {"plaintext": pt_b64})
    return {"ciphertext": d.get("data", {}).get("ciphertext", "")}


@app.post("/v1/crypto/decrypt")
def crypto_decrypt(req: CryptoReq):
    import base64
    if not req.ciphertext or not req.ciphertext.startswith("vault:"):
        return {"plaintext": req.ciphertext or ""}
    d = _bao_call(f"transit/decrypt/{_BAO_KEY}", {"ciphertext": req.ciphertext})
    pt_b64 = d.get("data", {}).get("plaintext", "")
    return {"plaintext": base64.b64decode(pt_b64).decode() if pt_b64 else ""}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
