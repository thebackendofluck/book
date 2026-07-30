# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Real-Time Matrix (RTMX) Service — FastAPI entrypoint
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
#
# Provides:
#   GET  /         - Welcome / health check
#   POST /streams/close/{group} - Close a Kafka consumer group stream
#   POST /caches/reload         - Reload in-memory caches
# =============================================================================

from __future__ import annotations

import logging
import os

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger()

app = FastAPI(title="RTMX Service", description="Real-Time Matrix monitoring service")


@app.get("/")
def index():
    return {"message": "Welcome to centralized event listener for Real-Time Matrix"}


@app.post("/streams/close/{group}")
def close_stream(group: str):
    """Signal a Kafka Streams consumer group to shut down."""
    # In production this interacts with the StreamsMonitor which tracks
    # running KafkaStreams instances and can call .close() on them.
    log.info("close_stream_requested", group=group)
    return {"closed": group}


@app.post("/caches/reload")
def repopulate_caches():
    """Reload all in-memory caches from the database."""
    # In production this calls CacheLoader.reload_cache() which
    # re-fetches MatrixScoreTypes, SupplierSettings, etc.
    log.info("cache_reload_requested")
    return {"reloaded": True}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
