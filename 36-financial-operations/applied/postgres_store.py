# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
psycopg3-backed IdempotencyStore implementation.

Not imported by the middleware package __init__ — the middleware is
datastore-agnostic. Import this in your FastAPI wiring code explicitly.
"""

from __future__ import annotations

import json
import time
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from .idempotency import (
    STATE_IN_PROGRESS,
    STATE_TERMINAL,
    IdempotencyRecord,
    IdempotencyStore,
)


class PostgresIdempotencyStore(IdempotencyStore):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _conn(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row)

    async def try_acquire(self, record: IdempotencyRecord) -> bool:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO idempotency_records
                        (key, user_id, path, body_hash, state,
                         created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s,
                            to_timestamp(%s), to_timestamp(%s))
                    ON CONFLICT (key) DO NOTHING
                    RETURNING key
                    """,
                    (
                        record.key,
                        record.user_id,
                        record.path,
                        record.body_hash,
                        STATE_IN_PROGRESS,
                        record.created_at,
                        record.expires_at,
                    ),
                )
                row = await cur.fetchone()
                await conn.commit()
                return row is not None

    async def fetch(self, key: str) -> Optional[IdempotencyRecord]:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT key, user_id, path, body_hash, state,
                           response_status, response_body, response_headers,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from expires_at) AS expires_at
                    FROM idempotency_records
                    WHERE key = %s AND expires_at > now()
                    """,
                    (key,),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        body = row["response_body"]
        headers_raw = row["response_headers"]
        headers = json.loads(headers_raw) if headers_raw else {}
        return IdempotencyRecord(
            key=row["key"],
            user_id=str(row["user_id"]) if row["user_id"] else None,
            path=row["path"],
            body_hash=row["body_hash"],
            state=row["state"],
            response_status=row["response_status"],
            response_body=bytes(body) if body is not None else None,
            response_headers=headers,
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    async def mark_terminal(
        self,
        key: str,
        status: int,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE idempotency_records
                    SET state = %s,
                        response_status = %s,
                        response_body = %s,
                        response_headers = %s
                    WHERE key = %s
                    """,
                    (
                        STATE_TERMINAL,
                        status,
                        body,
                        json.dumps(headers),
                        key,
                    ),
                )
                await conn.commit()

    async def release_inflight(self, key: str) -> None:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM idempotency_records
                    WHERE key = %s AND state = %s
                    """,
                    (key, STATE_IN_PROGRESS),
                )
                await conn.commit()

    async def purge_expired(self, now: float) -> int:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM idempotency_records WHERE expires_at < now()"
                )
                count = cur.rowcount
                await conn.commit()
        return int(count or 0)


__all__ = ["PostgresIdempotencyStore"]
_ = time  # keep import stable if later extended
