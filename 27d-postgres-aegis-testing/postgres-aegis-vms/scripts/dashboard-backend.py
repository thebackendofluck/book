#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
dashboard-backend.py — FastAPI server exposing postgres-aegis monitoring
data as JSON for the ops-dashboard (new.acmetocasino.com/dashboard.html).

All subprocess calls use list-form arguments (no shell=True) to prevent
command injection.

Endpoints:
  GET /api/aegis/mtls-status   <- /opt/aegis-monitoring/mtls-status.json
  GET /api/aegis/autoscaler    <- /opt/aegis-monitoring/autoscaler-decision.json
  GET /api/aegis/backup        <- pgbackrest info (ssh to each shard writer)
  GET /api/aegis/cluster       <- Patroni /patroni from both shard writers
  GET /api/aegis/hsm           <- YubiHSM connector status
  GET /healthz

Run:
  pip install fastapi uvicorn httpx
  uvicorn dashboard_backend:app --host 0.0.0.0 --port 8765

Deploy: runs on ops-host (10.0.0.11), proxied by Nginx to
new.acmetocasino.com/api/aegis/.
"""

import asyncio
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="postgres-aegis dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]  # Starlette ParamSpec false positive; see FastAPI CORS docs
    allow_origins=["https://new.acmetocasino.com", "http://localhost:3010"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Config — override via env vars
# ---------------------------------------------------------------------------
MON_DIR        = Path(os.environ.get("AEGIS_MON_DIR", "/opt/aegis-monitoring"))
SSH_OPTS       = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes"]
SHARD_A_WRITER = os.environ.get("SHARD_A_WRITER", "10.0.42.30")
SHARD_B_WRITER = os.environ.get("SHARD_B_WRITER", "10.0.42.32")
HSM_CONNECTOR  = os.environ.get("HSM_CONNECTOR", "http://127.0.0.1:12345")
PATRONI_PORT   = int(os.environ.get("PATRONI_PORT", "8008"))
SSH_USER       = os.environ.get("AEGIS_SSH_USER", "ansible")
PKCS11_LIB     = "/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"

STANZAS = {
    "casino-aegis":   SHARD_A_WRITER,
    "casino-aegis-b": SHARD_B_WRITER,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"{path.name} not yet written — collector may not have run",
        )
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Malformed JSON in {path.name}: {exc}") from exc


async def _ssh_json(host: str, remote_cmd: list[str]) -> dict:
    """Run remote_cmd on host over SSH; parse stdout as JSON.

    remote_cmd is a list — each element is a separate argv entry passed to
    ssh, so there is no shell interpolation of untrusted data.
    """
    proc = await asyncio.create_subprocess_exec(
        "ssh", *SSH_OPTS, f"{SSH_USER}@{host}", *remote_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=f"SSH to {host} failed: {stderr.decode()[:200]}")
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Non-JSON from {host}: {stdout.decode()[:200]}") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/aegis/mtls-status")
async def mtls_status():
    data = _read_json(MON_DIR / "mtls-status.json")
    return JSONResponse(content=data)


@app.get("/api/aegis/autoscaler")
async def autoscaler():
    data = _read_json(MON_DIR / "autoscaler-decision.json")
    return JSONResponse(content=data)


@app.get("/api/aegis/cluster")
async def cluster():
    results: dict = {}
    async with httpx.AsyncClient(timeout=5) as client:
        for shard, host in [("shard-a", SHARD_A_WRITER), ("shard-b", SHARD_B_WRITER)]:
            try:
                resp = await client.get(f"http://{host}:{PATRONI_PORT}/patroni")
                results[shard] = resp.json() if resp.status_code == 200 else {"error": resp.status_code}
            except Exception as exc:  # noqa: BLE001
                results[shard] = {"error": str(exc)}
    return JSONResponse(content={
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "shards": results,
    })


@app.get("/api/aegis/backup")
async def backup():
    results: dict = {}
    for stanza, host in STANZAS.items():
        try:
            # argv list — stanza name validated against known keys, not user input
            results[stanza] = await _ssh_json(
                host,
                ["sudo", "-u", "postgres", "pgbackrest",
                 f"--stanza={stanza}", "info", "--output=json"],
            )
        except Exception as exc:  # noqa: BLE001
            results[stanza] = {"error": str(exc)}
    return JSONResponse(content={
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stanzas": results,
    })


@app.get("/api/aegis/hsm")
async def hsm():
    result: dict = {"connector_url": HSM_CONNECTOR}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{HSM_CONNECTOR}/connector/status")
            result["connector_status"] = resp.text.strip()
            result["connector_ok"] = "status=OK" in resp.text
        except Exception as exc:  # noqa: BLE001
            result["connector_status"] = f"unreachable: {exc}"
            result["connector_ok"] = False

    # pkcs11-tool uses list-form argv — no shell injection possible
    try:
        proc = subprocess.run(  # noqa: S603
            ["pkcs11-tool", "--module", PKCS11_LIB, "--list-objects"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        result["objects_raw"] = proc.stdout[:2000]
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        result["objects_raw"] = f"pkcs11-tool not available: {exc}"

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Systemd service unit — print with: python3 dashboard-backend.py --print-service
# ---------------------------------------------------------------------------

SERVICE_UNIT = """
[Unit]
Description=postgres-aegis dashboard backend API
After=network-online.target

[Service]
Type=exec
User=root
WorkingDirectory=/opt/aegis-monitoring
Environment=AEGIS_MON_DIR=/opt/aegis-monitoring
Environment=SHARD_A_WRITER=10.0.42.30
Environment=SHARD_B_WRITER=10.0.42.32
Environment=HSM_CONNECTOR=http://127.0.0.1:12345
ExecStart=/usr/bin/uvicorn dashboard_backend:app --host 0.0.0.0 --port 8765 --workers 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

if __name__ == "__main__":
    import sys
    if "--print-service" in sys.argv:
        print(SERVICE_UNIT.strip())
        sys.exit(0)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=True)
