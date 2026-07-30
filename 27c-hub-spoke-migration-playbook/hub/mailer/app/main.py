# Companion code for "The Backend of Luck" - Chapter 27c, Migrating a Single-Jurisdiction Casino Platform to Hub & Spo.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Hub mailer service.

Stub — no actual email is sent. Renders a template (loaded from ConfigMap)
and logs the body. Enforces per-jurisdiction opt-in for non-transactional
categories. Transactional templates (e.g., dsr_ack) always send.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mailer")

PG_DSN = os.environ["PG_DSN"]
TEMPLATE_DIR = Path(os.environ.get("TEMPLATE_DIR", "/templates"))

# Category per template (transactional bypass opt-in).
TEMPLATE_CATEGORY = {
    "dsr_ack": "transactional",
    "deposit_confirmed": "transactional",
    "erasure_completed": "transactional",
    "newsletter": "newsletter",
    "promo": "marketing",
}

app = FastAPI(title="hub-mailer")
_jinja = Environment(undefined=StrictUndefined, autoescape=False)


def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(PG_DSN)


def load_template(name: str) -> str:
    path = TEMPLATE_DIR / f"{name}.txt"
    if not path.exists():
        raise HTTPException(404, f"template {name!r} not found")
    return path.read_text()


class SendBody(BaseModel):
    to_player_global_id: str
    template: str
    jurisdiction: str
    data: dict = {}


class OptInBody(BaseModel):
    global_id: str
    jurisdiction: str
    category: str  # transactional | newsletter | marketing
    granted: bool


@app.get("/v1/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/opt-in")
def set_opt_in(body: OptInBody) -> dict:
    if body.category not in {"transactional", "newsletter", "marketing"}:
        raise HTTPException(400, "invalid category")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mailer_opt_in (global_id, jurisdiction, category, granted, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (global_id, jurisdiction, category)
            DO UPDATE SET granted = EXCLUDED.granted, updated_at = now()
            """,
            (body.global_id, body.jurisdiction, body.category, body.granted),
        )
        conn.commit()
    return {"ok": True}


def _opt_in_granted(global_id: str, jurisdiction: str, category: str) -> Optional[bool]:
    """None if no record, else bool."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT granted FROM mailer_opt_in "
            "WHERE global_id=%s AND jurisdiction=%s AND category=%s",
            (global_id, jurisdiction, category),
        )
        row = cur.fetchone()
        return None if row is None else bool(row["granted"])


@app.post("/v1/send")
def send(body: SendBody) -> dict:
    category = TEMPLATE_CATEGORY.get(body.template)
    if category is None:
        raise HTTPException(400, f"unknown template {body.template!r}")

    if category != "transactional":
        granted = _opt_in_granted(body.to_player_global_id, body.jurisdiction, category)
        if not granted:
            log.info(
                "suppressed template=%s gid=%s jur=%s category=%s",
                body.template, body.to_player_global_id, body.jurisdiction, category,
            )
            return {"sent": False, "suppressed": True, "reason": "opt_in_missing_or_denied"}

    raw = load_template(body.template)
    rendered = _jinja.from_string(raw).render(**body.data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mailer_sent (global_id, jurisdiction, template, rendered_body) "
            "VALUES (%s, %s, %s, %s)",
            (body.to_player_global_id, body.jurisdiction, body.template, rendered),
        )
        conn.commit()
    log.info(
        "SEND (stub) template=%s gid=%s jur=%s body=%r",
        body.template, body.to_player_global_id, body.jurisdiction, rendered,
    )
    return {"sent": True, "suppressed": False, "rendered_body": rendered}
