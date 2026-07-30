# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AcmeToCasino Modular Monolith Platform.

A domain-driven design backend using FastAPI with:
  - Player Account Management (PAM)
  - Event-Sourced Wallet
  - Game Aggregation Layer (GAL) with CSPRNG
  - Compliance (KYC/AML)
  - Responsible Gaming
  - Game Control (server-side RTP)
  - Event Bus (Redis Pub/Sub)
  - WebSocket for real-time updates
"""

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.database import close_pool, get_cursor, init_pool, run_migrations
from app.logging_config import (
    correlation_id_var,
    new_correlation_id,
    setup_logging,
)
from app.metrics import (
    get_metrics,
    get_metrics_content_type,
    http_request_duration_seconds,
    http_requests_total,
    websocket_connections,
)
from app.redis_client import close_redis, get_redis, init_redis

from app.pam.router import router as pam_router
from app.wallet.router import router as wallet_router
from app.gal.router import router as gal_router
from app.compliance.router import router as compliance_router
from app.responsible_gaming.router import router as rg_router
from app.game_control.router import router as gc_router

setup_logging(json_output=not settings.DEBUG)
logger = logging.getLogger(__name__)

_startup_time: float = 0.0


# ---------- WebSocket connection manager ----------

class ConnectionManager:
    """Manages WebSocket connections for real-time event broadcasting."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        websocket_connections.inc()
        logger.info("WebSocket client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        websocket_connections.dec()
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active))

    async def broadcast(self, message: dict):
        payload = json.dumps(message, default=str)
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.active.remove(ws)


manager = ConnectionManager()


def _redis_subscriber_thread(loop: asyncio.AbstractEventLoop):
    """
    Background thread that subscribes to all Redis event channels
    and forwards messages to WebSocket clients.
    """
    try:
        r = get_redis()
        pubsub = r.pubsub()
        channels = [
            "player.events",
            "wallet.transactions",
            "game.rounds",
            "compliance.alerts",
        ]
        pubsub.subscribe(*channels)
        logger.info("Redis subscriber started on channels: %s", channels)

        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                data = {"raw": message["data"]}

            asyncio.run_coroutine_threadsafe(manager.broadcast(data), loop)
    except Exception:
        logger.exception("Redis subscriber thread crashed")


# ---------- Lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _startup_time
    _startup_time = time.time()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # Initialize infrastructure
    init_pool()
    run_migrations()
    init_redis()

    # Start Redis subscriber in background thread
    loop = asyncio.get_running_loop()
    subscriber = threading.Thread(
        target=_redis_subscriber_thread,
        args=(loop,),
        daemon=True,
    )
    subscriber.start()

    logger.info("Platform ready")
    yield

    # Shutdown
    close_redis()
    close_pool()
    logger.info("Platform shut down")


# ---------- App ----------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Modular monolith iGambling platform with DDD architecture",
    lifespan=lifespan,
    root_path="/api/v2",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register domain routers
app.include_router(pam_router)
app.include_router(wallet_router)
app.include_router(gal_router)
app.include_router(compliance_router)
app.include_router(rg_router)
app.include_router(gc_router)


# ---------- Metrics middleware ----------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Track request count and duration for every HTTP request."""
    # Set correlation ID for structured logging
    cid = request.headers.get("X-Correlation-ID", new_correlation_id())
    correlation_id_var.set(cid)

    method = request.method
    path = request.url.path

    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    # Skip metrics endpoint itself to avoid self-referencing noise
    if path != "/metrics":
        http_requests_total.labels(
            method=method, endpoint=path, status=response.status_code,
        ).inc()
        http_request_duration_seconds.labels(
            method=method, endpoint=path,
        ).observe(duration)

    response.headers["X-Correlation-ID"] = cid
    return response


# ---------- Core endpoints ----------

@app.get("/metrics", tags=["System"], include_in_schema=False)
def prometheus_metrics():
    """Prometheus metrics endpoint."""
    return Response(
        content=get_metrics(),
        media_type=get_metrics_content_type(),
    )


@app.get("/health", tags=["System"])
def health_check():
    """Comprehensive platform health check with component details."""
    result = {
        "status": "healthy",
        "database": {"status": "unknown"},
        "redis": {"status": "unknown"},
        "services": {
            "pam": "operational",
            "wallet": "operational",
            "gal": "operational",
            "compliance": "operational",
            "responsible_gaming": "operational",
            "game_control": "operational",
        },
        "uptime_seconds": round(time.time() - _startup_time, 1) if _startup_time else 0,
        "version": settings.APP_VERSION,
    }

    # Database health
    active_sessions = 0
    try:
        with get_cursor() as cur:
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            db_latency = round((time.perf_counter() - t0) * 1000, 1)

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM game_sessions WHERE status = 'active'"
            )
            active_sessions = cur.fetchone()["cnt"]

        from app.database import _pool
        pool_size = _pool.maxconn if _pool else 0
        result["database"] = {
            "status": "connected",
            "latency_ms": db_latency,
            "pool_size": pool_size,
            "active_connections": pool_size - (len(_pool._pool) if _pool and hasattr(_pool, "_pool") else 0),
        }
    except Exception as exc:
        result["database"] = {"status": f"error: {exc}"}
        result["status"] = "degraded"

    # Redis health
    try:
        r = get_redis()
        t0 = time.perf_counter()
        r.ping()
        redis_latency = round((time.perf_counter() - t0) * 1000, 1)

        info = r.info(section="memory")
        clients_info = r.info(section="clients")
        result["redis"] = {
            "status": "connected",
            "latency_ms": redis_latency,
            "memory_used": info.get("used_memory_human", "unknown"),
            "connected_clients": clients_info.get("connected_clients", 0),
            "keyspace_size": r.dbsize(),
        }
    except Exception as exc:
        result["redis"] = {"status": f"error: {exc}"}
        result["status"] = "degraded"

    # Request stats from Prometheus metrics
    total_requests = 0.0
    for metric in http_requests_total.collect():
        for sample in metric.samples:
            if sample.name == "http_requests_total_total":
                total_requests += sample.value

    result["request_stats"] = {
        "total_requests": int(total_requests),
        "active_game_sessions": active_sessions,
    }

    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)


@app.get("/stats", tags=["System"])
def analytics_dw():
    """
    Aggregate platform statistics for the dashboard.
    Returns player counts, wallet totals, game stats, and compliance summaries.
    """
    stats: dict = {}

    try:
        with get_cursor() as cur:
            # Player counts
            cur.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE status = 'active') AS active "
                "FROM players"
            )
            row = cur.fetchone()
            stats["players"] = {"total": row["total"], "active": row["active"]}

            # Wallet events by type
            cur.execute(
                "SELECT event_type, COUNT(*) AS cnt, "
                "COALESCE(SUM(amount), 0) AS total_amount "
                "FROM wallet_events GROUP BY event_type ORDER BY event_type"
            )
            wallet_rows = cur.fetchall()
            stats["wallet_events"] = {
                r["event_type"]: {"count": r["cnt"], "total_amount": str(r["total_amount"])}
                for r in wallet_rows
            }

            # Total deposits / withdrawals
            cur.execute(
                "SELECT "
                "COALESCE(SUM(amount) FILTER (WHERE event_type = 'DEPOSIT'), 0) AS deposits, "
                "COALESCE(SUM(amount) FILTER (WHERE event_type = 'WITHDRAWAL'), 0) AS withdrawals "
                "FROM wallet_events"
            )
            dw = cur.fetchone()
            stats["total_deposits"] = str(dw["deposits"])
            stats["total_withdrawals"] = str(dw["withdrawals"])

            # Game rounds
            cur.execute(
                "SELECT COUNT(*) AS total_rounds, "
                "COALESCE(AVG(bet_amount), 0) AS avg_bet "
                "FROM game_rounds"
            )
            gr = cur.fetchone()
            stats["game_rounds"] = {
                "total": gr["total_rounds"],
                "average_bet": str(round(gr["avg_bet"], 2)),
            }

            # Active game sessions
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM game_sessions WHERE status = 'active'"
            )
            stats["active_game_sessions"] = cur.fetchone()["cnt"]

            # AML alerts by status
            cur.execute(
                "SELECT status, COUNT(*) AS cnt "
                "FROM aml_alerts GROUP BY status ORDER BY status"
            )
            stats["aml_alerts"] = {r["status"]: r["cnt"] for r in cur.fetchall()}

            # KYC checks by status
            cur.execute(
                "SELECT status, COUNT(*) AS cnt "
                "FROM kyc_checks GROUP BY status ORDER BY status"
            )
            stats["kyc_checks"] = {r["status"]: r["cnt"] for r in cur.fetchall()}

    except Exception as exc:
        logger.exception("Failed to collect platform stats")
        return JSONResponse(
            content={"error": str(exc)},
            status_code=500,
        )

    return JSONResponse(content=stats)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket endpoint for real-time event streaming.
    Clients receive events from all Redis Pub/Sub channels.
    """
    await manager.connect(ws)
    try:
        while True:
            # Keep connection alive; clients can also send messages
            data = await ws.receive_text()
            # Echo back for ping/pong support
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(ws)
