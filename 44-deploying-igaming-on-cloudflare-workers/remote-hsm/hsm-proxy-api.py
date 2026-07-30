# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import httpx, os, base64, hmac, secrets, time, json, hashlib
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # ty:ignore[unresolved-import]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


app = FastAPI(title="HSM Proxy API v2", lifespan=lifespan)
API_KEY = os.environ.get("HSM_API_KEY", "")
BAO_ADDR = os.environ.get("BAO_ADDR", "https://127.0.0.1:8200")
BAO_TOKEN = os.environ.get("BAO_TOKEN", "")

# ============================================================
# In-memory DEK cache (envelope encryption)
# HSM unwraps DEK on startup; local AES-256-GCM for encrypt/decrypt
# Sign always goes to HSM (private key never leaves hardware)
# ============================================================
_dek_cache = {}  # key_name -> {"dek": bytes, "version": int, "loaded_at": float}
_dek_lock = False

# Persistent HTTP client (connection pooling)
_http_client = None

def get_client():
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            verify=os.getenv("BAO_CACERT", True),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            timeout=httpx.Timeout(10.0, connect=2.0)
        )
    return _http_client

async def _bao_request(method, path, payload=None):
    c = get_client()
    headers = {"X-Vault-Token": BAO_TOKEN}
    if method == "GET":
        r = await c.get(BAO_ADDR + path, headers=headers)
    else:
        r = await c.post(BAO_ADDR + path, headers=headers, json=payload or {})
    return r

async def _load_dek(key_name):
    """Generate a local DEK, wrap it with Transit, store wrapped+unwrapped"""
    if key_name in _dek_cache:
        entry = _dek_cache[key_name]
        if time.time() - entry["loaded_at"] < 3600:  # 1h cache
            return entry
    # Generate random 256-bit DEK
    dek = secrets.token_bytes(32)
    dek_b64 = base64.b64encode(dek).decode()
    # Wrap DEK with Transit (HSM-backed)
    r = await _bao_request("POST", "/v1/transit/encrypt/" + key_name,
                           {"plaintext": dek_b64})
    if r.status_code != 200:
        raise HTTPException(502, "Failed to wrap DEK: " + r.text)
    wrapped = r.json()["data"]["ciphertext"]
    # Read key version
    kr = await _bao_request("GET", "/v1/transit/keys/" + key_name)
    version = 1
    if kr.status_code == 200:
        version = kr.json().get("data", {}).get("latest_version", 1)
    entry = {
        "dek": dek,
        "aesgcm": AESGCM(dek),
        "wrapped_dek": wrapped,
        "version": version,
        "loaded_at": time.time(),
        "key_name": key_name,
    }
    _dek_cache[key_name] = entry
    return entry

def _local_encrypt(aesgcm, plaintext_b64):
    """AES-256-GCM encrypt locally using cached DEK"""
    plaintext = base64.b64decode(plaintext_b64)
    nonce = secrets.token_bytes(12)  # 96-bit nonce
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # Format: base64(nonce + ciphertext)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode()

def _local_decrypt(aesgcm, ciphertext_b64):
    """AES-256-GCM decrypt locally using cached DEK"""
    combined = base64.b64decode(ciphertext_b64)
    nonce = combined[:12]
    ciphertext = combined[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return base64.b64encode(plaintext).decode()

# ============================================================
# Metrics
# ============================================================
_metrics: Dict[str, Any] = {
    "encrypt": deque(maxlen=1000),
    "decrypt": deque(maxlen=1000),
    "sign": deque(maxlen=1000),
    "random": deque(maxlen=1000),
    "total_requests": 0,
    "total_errors": 0,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "mode": "envelope"  # "envelope" = local DEK, "transit" = HSM for everything
}

def _record(op, ms, ok=True):
    _metrics[op].append({"ts": time.time(), "ms": ms, "ok": ok})
    _metrics["total_requests"] += 1
    if not ok:
        _metrics["total_errors"] += 1

def _pctl(arr, p):
    if not arr:
        return 0
    s = sorted(arr)
    return round(s[min(int(len(s) * p / 100), len(s) - 1)], 2)

def check_key(key):
    if not hmac.compare_digest(key, API_KEY):
        raise HTTPException(401, "Invalid API key")

# ============================================================
# Models
# ============================================================
class EncryptReq(BaseModel):
    plaintext: str
    key_name: str = "field-cipher"

class DecryptReq(BaseModel):
    ciphertext: str
    key_name: str = "field-cipher"

class SignReq(BaseModel):
    input: str
    key_name: str = "jwt-signing"

class BatchEncryptReq(BaseModel):
    batch_input: list
    key_name: str = "field-cipher"

# ============================================================
# Endpoints
# ============================================================
@app.get("/hsm/health")
async def health():
    try:
        r = await _bao_request("GET", "/v1/sys/health")
        dek_keys = list(_dek_cache.keys())
        return {
            "status": "ok",
            "bao": r.status_code == 200,
            "mode": "envelope_encryption",
            "cached_deks": len(dek_keys),
            "dek_keys": dek_keys
        }
    except Exception:
        return {"status": "degraded", "bao": False, "mode": "envelope_encryption"}

@app.get("/hsm/metrics")
async def get_metrics():
    now = time.time()
    w5 = now - 300
    result = {}
    for op in ["encrypt", "decrypt", "sign", "random"]:
        recent = [m["ms"] for m in _metrics[op] if m["ts"] > w5 and m["ok"]]
        errors = sum(1 for m in _metrics[op] if m["ts"] > w5 and not m["ok"])
        result[op] = {
            "count_5m": len(recent),
            "errors_5m": errors,
            "p50": _pctl(recent, 50),
            "p95": _pctl(recent, 95),
            "p99": _pctl(recent, 99),
            "avg": round(sum(recent) / len(recent), 2) if recent else 0
        }
    return {
        "operations": result,
        "total_requests": _metrics["total_requests"],
        "total_errors": _metrics["total_errors"],
        "error_rate": round(_metrics["total_errors"] / max(_metrics["total_requests"], 1) * 100, 2),
        "uptime_since": _metrics["started_at"],
        "mode": "envelope_encryption",
        "cached_deks": len(_dek_cache),
        "window": "5m"
    }

@app.post("/hsm/encrypt")
async def encrypt(req: EncryptReq, x_api_key: str = Header(...)):
    check_key(x_api_key)
    t0 = time.monotonic()
    try:
        entry = await _load_dek(req.key_name)
        ct = _local_encrypt(entry["aesgcm"], req.plaintext)
        # Prefix with version so we know which DEK was used
        result = "local:v%d:%s" % (entry["version"], ct)
        _record("encrypt", (time.monotonic() - t0) * 1000)
        return {"ciphertext": result, "key_version": entry["version"], "mode": "envelope"}
    except HTTPException:
        raise
    except Exception as e:
        _record("encrypt", (time.monotonic() - t0) * 1000, False)
        raise HTTPException(502, str(e))

@app.post("/hsm/decrypt")
async def decrypt(req: DecryptReq, x_api_key: str = Header(...)):
    check_key(x_api_key)
    t0 = time.monotonic()
    try:
        ct = req.ciphertext
        if ct.startswith("local:"):
            # Local envelope decryption
            parts = ct.split(":", 2)
            ct_b64 = parts[2]
            entry = await _load_dek(req.key_name)
            pt = _local_decrypt(entry["aesgcm"], ct_b64)
            _record("decrypt", (time.monotonic() - t0) * 1000)
            return {"plaintext": pt, "mode": "envelope"}
        else:
            # Legacy Transit ciphertext (vault:v1:...)
            r = await _bao_request("POST", "/v1/transit/decrypt/" + req.key_name,
                                   {"ciphertext": ct})
            if r.status_code != 200:
                _record("decrypt", (time.monotonic() - t0) * 1000, False)
                raise HTTPException(502, "Transit error: " + r.text)
            _record("decrypt", (time.monotonic() - t0) * 1000)
            return r.json()["data"]
    except HTTPException:
        raise
    except Exception as e:
        _record("decrypt", (time.monotonic() - t0) * 1000, False)
        raise HTTPException(502, str(e))

@app.post("/hsm/encrypt/batch")
async def batch_encrypt(req: BatchEncryptReq, x_api_key: str = Header(...)):
    check_key(x_api_key)
    t0 = time.monotonic()
    try:
        entry = await _load_dek(req.key_name)
        results = []
        for item in req.batch_input:
            pt = item.get("plaintext", "")
            ct = _local_encrypt(entry["aesgcm"], pt)
            results.append({
                "ciphertext": "local:v%d:%s" % (entry["version"], ct),
                "key_version": entry["version"]
            })
        _record("encrypt", (time.monotonic() - t0) * 1000)
        return {"batch_results": results, "count": len(results), "mode": "envelope"}
    except Exception as e:
        _record("encrypt", (time.monotonic() - t0) * 1000, False)
        raise HTTPException(502, str(e))

@app.post("/hsm/sign")
async def sign(req: SignReq, x_api_key: str = Header(...)):
    """Sign always goes to HSM - private key never leaves hardware"""
    check_key(x_api_key)
    t0 = time.monotonic()
    try:
        r = await _bao_request("POST", "/v1/transit/sign/" + req.key_name,
                               {"input": req.input})
        if r.status_code != 200:
            _record("sign", (time.monotonic() - t0) * 1000, False)
            raise HTTPException(502, "Transit error: " + r.text)
        _record("sign", (time.monotonic() - t0) * 1000)
        return r.json()["data"]
    except HTTPException:
        raise
    except Exception as e:
        _record("sign", (time.monotonic() - t0) * 1000, False)
        raise HTTPException(502, str(e))

@app.post("/hsm/random")
async def random_bytes(count: int = 32, x_api_key: str = Header(...)):
    check_key(x_api_key)
    t0 = time.monotonic()
    result = {"random_bytes": base64.b64encode(secrets.token_bytes(count)).decode()}
    _record("random", (time.monotonic() - t0) * 1000)
    return result

@app.post("/hsm/rewrap")
async def rewrap_dek(key_name: str = "field-cipher", x_api_key: str = Header(...)):
    """Force re-wrap DEK with latest Transit key version (for key rotation)"""
    check_key(x_api_key)
    if key_name in _dek_cache:
        del _dek_cache[key_name]
    entry = await _load_dek(key_name)
    return {
        "key_name": key_name,
        "version": entry["version"],
        "rewrapped": True,
        "wrapped_dek_prefix": entry["wrapped_dek"][:30] + "..."
    }

