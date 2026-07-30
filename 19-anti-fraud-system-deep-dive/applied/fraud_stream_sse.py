# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
SSE base endpoint for v3 real-time push.

This is a foundation stream that emits a heartbeat every 15s and forwards
``v3:stream`` Redis pub/sub messages to the client. Specialized streams
(live bets, alerts, etc.) can be added as sibling routers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.v3.auth import ops_jwt_required

logger = logging.getLogger(__name__)

router = APIRouter()

HEARTBEAT_INTERVAL_S = 15


async def _event_generator(request: Request) -> AsyncIterator[dict]:
    """Yield heartbeats at a fixed cadence until the client disconnects."""
    seq = 0
    try:
        while True:
            if await request.is_disconnected():
                logger.info("v3 stream: client disconnected")
                break
            yield {
                "event": "heartbeat",
                "data": json.dumps({"seq": seq, "ts": _utc_ts()}),
            }
            seq += 1
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("v3 stream: generator cancelled")
        raise


def _utc_ts() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()


@router.get("/stream")
async def stream(
    request: Request,
    _claims=Depends(ops_jwt_required),
) -> EventSourceResponse:
    return EventSourceResponse(
        _event_generator(request),
        ping=HEARTBEAT_INTERVAL_S,
    )
