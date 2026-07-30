# Companion code for "The Backend of Luck" - Chapter 27c, Migrating a Single-Jurisdiction Casino Platform to Hub & Spo.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Hub global-id service.

Owns the authoritative mapping of local_player_id (per jurisdiction) to a
global_id UUID and the player exclusion state. Publishes exclusion events on
Redis channel `hub:exclusion:sync` so spokes can mirror state.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional

import psycopg2
import psycopg2.extras
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("global-id")

PG_DSN = os.environ["PG_DSN"]
REDIS_URL = os.environ["REDIS_URL"]
EXCLUSION_CHANNEL = "hub:exclusion:sync"

app = FastAPI(title="hub-global-id")
_redis: Optional[redis.Redis] = None


def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(PG_DSN)


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


class CreatePlayerBody(BaseModel):
    jurisdiction: str
    local_player_id: str  # uuid string supplied by spoke


class ExcludeBody(BaseModel):
    reason: str
    scope: str  # 'local' | 'global'
    jurisdiction: Optional[str] = None


@app.get("/v1/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/readyz")
def readyz() -> dict:
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
        get_redis().ping()
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/v1/players")
def create_player(body: CreatePlayerBody) -> dict:
    local_uuid = uuid.UUID(body.local_player_id)
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Idempotent: if (jurisdiction, local_player_id) already exists, return it.
        cur.execute(
            "SELECT global_id FROM jurisdiction_accounts "
            "WHERE jurisdiction=%s AND local_player_id=%s",
            (body.jurisdiction, str(local_uuid)),
        )
        row = cur.fetchone()
        if row:
            return {"global_id": str(row["global_id"]), "created": False}

        cur.execute("INSERT INTO global_players DEFAULT VALUES RETURNING global_id")
        global_id = cur.fetchone()["global_id"]
        cur.execute(
            "INSERT INTO jurisdiction_accounts (jurisdiction, local_player_id, global_id) "
            "VALUES (%s, %s, %s)",
            (body.jurisdiction, str(local_uuid), str(global_id)),
        )
        conn.commit()
        log.info("created global_id=%s jurisdiction=%s", global_id, body.jurisdiction)
        return {"global_id": str(global_id), "created": True}


@app.post("/v1/players/{global_id}/exclude")
def exclude(global_id: str, body: ExcludeBody) -> dict:
    if body.scope not in {"local", "global"}:
        raise HTTPException(400, "scope must be 'local' or 'global'")
    if body.scope == "local" and not body.jurisdiction:
        raise HTTPException(400, "jurisdiction required when scope=local")

    new_status = "globally_excluded" if body.scope == "global" else "local_excluded"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM global_players WHERE global_id=%s", (global_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "unknown global_id")
        cur.execute(
            "INSERT INTO exclusion_records (global_id, scope, jurisdiction, reason) "
            "VALUES (%s, %s, %s, %s)",
            (global_id, body.scope, body.jurisdiction, body.reason),
        )
        cur.execute(
            "UPDATE global_players SET exclusion_status=%s WHERE global_id=%s",
            (new_status, global_id),
        )
        conn.commit()

    event = {
        "global_id": global_id,
        "scope": body.scope,
        "jurisdiction": body.jurisdiction,
        "reason": body.reason,
        "ts": int(time.time()),
    }
    get_redis().publish(EXCLUSION_CHANNEL, json.dumps(event))
    log.info("exclusion published %s", event)
    return {"global_id": global_id, "status": new_status}


@app.get("/v1/players/by-local")
def by_local(jurisdiction: str, local_player_id: str) -> dict:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT global_id FROM jurisdiction_accounts "
            "WHERE jurisdiction=%s AND local_player_id=%s",
            (jurisdiction, local_player_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        return {"global_id": str(row["global_id"])}


@app.get("/v1/players/{global_id}")
def get_player(global_id: str) -> dict:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM global_players WHERE global_id=%s", (global_id,))
        gp = cur.fetchone()
        if not gp:
            raise HTTPException(404, "not found")
        cur.execute(
            "SELECT jurisdiction, local_player_id FROM jurisdiction_accounts WHERE global_id=%s",
            (global_id,),
        )
        accounts = [
            {"jurisdiction": r["jurisdiction"], "local_player_id": str(r["local_player_id"])}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT scope, jurisdiction, reason, created_at FROM exclusion_records "
            "WHERE global_id=%s ORDER BY id",
            (global_id,),
        )
        exclusions = [
            {
                "scope": r["scope"],
                "jurisdiction": r["jurisdiction"],
                "reason": r["reason"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in cur.fetchall()
        ]
    return {
        "global_id": str(gp["global_id"]),
        "exclusion_status": gp["exclusion_status"],
        "jurisdiction_accounts": accounts,
        "exclusion_records": exclusions,
    }
