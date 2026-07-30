# Companion code for "The Backend of Luck" - Chapter 27c, Migrating a Single-Jurisdiction Casino Platform to Hub & Spo.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Spoke-BR wallet service.

- Creates local BR players and registers them with the hub global-id service.
- Handles local deposits (rejected if player is excluded).
- Subscribes to hub Redis channel `hub:exclusion:sync`; when a matching
  global_id is excluded elsewhere, flips local status and inserts a BLOCK
  wallet event with blocked=true for that player.
- Supports local exclusion that also escalates to global at the hub.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import psycopg2
import psycopg2.extras
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("wallet-br")

PG_DSN = os.environ["PG_DSN"]
HUB_REDIS_URL = os.environ["HUB_REDIS_URL"]
HUB_GLOBAL_ID_URL = os.environ["HUB_GLOBAL_ID_URL"].rstrip("/")
JURISDICTION = os.environ.get("JURISDICTION", "BR")
EXCLUSION_CHANNEL = "hub:exclusion:sync"


def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(PG_DSN)


def _apply_global_exclusion(global_id: str, scope: str, reason: str | None) -> None:
    """Apply a hub exclusion event locally."""
    new_status = "globally_excluded" if scope == "global" else "local_excluded"
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT local_player_id FROM players WHERE global_id=%s",
            (global_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return  # not our player
        for r in rows:
            cur.execute(
                "UPDATE players SET status=%s WHERE local_player_id=%s",
                (new_status, r["local_player_id"]),
            )
            cur.execute(
                "INSERT INTO wallet_events (local_player_id, event_type, amount_cents, blocked, jurisdiction) "
                "VALUES (%s, 'BLOCK', 0, true, %s)",
                (r["local_player_id"], JURISDICTION),
            )
        conn.commit()
    log.info("applied exclusion locally global_id=%s scope=%s reason=%r", global_id, scope, reason)


def _subscribe_loop() -> None:
    backoff = 1.0
    while True:
        try:
            r = redis.Redis.from_url(HUB_REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(EXCLUSION_CHANNEL)
            log.info("subscribed to %s", EXCLUSION_CHANNEL)
            backoff = 1.0
            for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    event = json.loads(msg["data"])
                    _apply_global_exclusion(
                        event["global_id"], event.get("scope", "global"), event.get("reason"),
                    )
                except Exception:  # noqa: BLE001
                    log.exception("failed to process exclusion event")
        except Exception:  # noqa: BLE001
            log.exception("subscribe loop error; retrying in %.1fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    t = threading.Thread(target=_subscribe_loop, daemon=True, name="hub-exclusion-sub")
    t.start()
    yield


app = FastAPI(title="spoke-br-wallet", lifespan=lifespan)


class CreatePlayerBody(BaseModel):
    local_player_id: str | None = None  # optional, server will generate if absent


class DepositBody(BaseModel):
    player_id: str
    amount_cents: int


class ExcludeBody(BaseModel):
    reason: str
    escalate_global: bool = False


@app.get("/v1/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/players")
def create_player(body: CreatePlayerBody) -> dict:
    local_id = uuid.UUID(body.local_player_id) if body.local_player_id else uuid.uuid4()
    # Register with hub first to obtain global_id.
    resp = httpx.post(
        f"{HUB_GLOBAL_ID_URL}/v1/players",
        json={"jurisdiction": JURISDICTION, "local_player_id": str(local_id)},
        timeout=10.0,
    )
    resp.raise_for_status()
    global_id = resp.json()["global_id"]

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO players (local_player_id, global_id, jurisdiction) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (local_player_id) DO NOTHING",
            (str(local_id), global_id, JURISDICTION),
        )
        conn.commit()
    return {"local_player_id": str(local_id), "global_id": global_id}


@app.post("/v1/wallet/deposit")
def deposit(body: DepositBody) -> dict:
    if body.amount_cents <= 0:
        raise HTTPException(400, "amount must be positive")
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT status FROM players WHERE local_player_id=%s", (body.player_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "unknown player")
        if row["status"] != "active":
            raise HTTPException(
                status_code=403,
                detail={"error": "player_excluded", "status": row["status"]},
            )
        cur.execute(
            "INSERT INTO wallet_events (local_player_id, event_type, amount_cents, jurisdiction) "
            "VALUES (%s, 'DEPOSIT', %s, %s) RETURNING id",
            (body.player_id, body.amount_cents, JURISDICTION),
        )
        event_id = cur.fetchone()["id"]
        conn.commit()
    return {"event_id": event_id, "amount_cents": body.amount_cents}


@app.get("/v1/wallet/balance")
def balance(player_id: str) -> dict:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status FROM players WHERE local_player_id=%s", (player_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "unknown player")
        cur.execute(
            """
            SELECT COALESCE(SUM(
              CASE event_type
                WHEN 'DEPOSIT' THEN amount_cents
                WHEN 'WIN' THEN amount_cents
                WHEN 'WITHDRAWAL' THEN -amount_cents
                WHEN 'BET' THEN -amount_cents
                ELSE 0 END
            ), 0) AS bal
            FROM wallet_events WHERE local_player_id=%s AND blocked=false
            """,
            (player_id,),
        )
        bal = cur.fetchone()["bal"]
    return {"player_id": player_id, "balance_cents": int(bal), "status": row["status"]}


@app.post("/v1/players/{player_id}/exclude")
def exclude_local(player_id: str, body: ExcludeBody) -> dict:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT global_id FROM players WHERE local_player_id=%s", (player_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "unknown player")
        global_id = str(row["global_id"])
        cur.execute(
            "UPDATE players SET status='local_excluded' WHERE local_player_id=%s",
            (player_id,),
        )
        cur.execute(
            "INSERT INTO wallet_events (local_player_id, event_type, amount_cents, blocked, jurisdiction) "
            "VALUES (%s, 'BLOCK', 0, true, %s)",
            (player_id, JURISDICTION),
        )
        conn.commit()

    if body.escalate_global:
        httpx.post(
            f"{HUB_GLOBAL_ID_URL}/v1/players/{global_id}/exclude",
            json={"reason": body.reason, "scope": "global"},
            timeout=10.0,
        ).raise_for_status()
    return {"player_id": player_id, "global_id": global_id, "escalated": body.escalate_global}
