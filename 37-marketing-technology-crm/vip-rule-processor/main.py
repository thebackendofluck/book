# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
VIP Rule Processor -- FastAPI application.

Exposes REST endpoints for on-demand VIP tier recalculation and rule management.
The processor also runs two background Kafka consumers and a nightly scheduler.

Endpoints:
  POST /users/{user_id}/recalculate  -- trigger recalculation for a single user
  GET  /rules                        -- list active VIP rules for a brand
  GET  /healthz                      -- health check

Configuration is via environment variables (see AppConfig / DbConfig / KafkaConfig).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .models import (
    AppConfig,
    BrandId,
    DbConfig,
    HttpConfig,
    KafkaConfig,
    RecalculateCommand,
    SchedulerConfig,
    UserId,
)
from .service import (
    EvaluateUserStatus,
    RecalculationCommandProcessor,
    SchedulerFlow,
    TransactionsEventProcessor,
    VipRepository,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Stub repository (swap with a real DB-backed implementation)
# ---------------------------------------------------------------------------

class StubVipRepository(VipRepository):
    """In-memory stub — replace with a real SQLAlchemy-backed repository."""

    async def get_rules_for_brand(self, brand_id: BrandId):  # type: ignore[override]
        return []

    async def get_player_activity(self, user_id: UserId, brand_id: BrandId):  # type: ignore[override]
        from datetime import datetime, timezone

        from .models import PlayerActivity

        return PlayerActivity(
            user_id=user_id,
            total_bet_volume=0,
            total_deposit_volume=0,
            total_withdrawal_volume=0,
            net_deposit_volume=0,
            bet_count=0,
            deposit_count=0,
            active_days=0,
            account_age_days=0,
        )

    async def get_current_status(self, user_id: UserId, brand_id: BrandId):  # type: ignore[override]
        return None

    async def save_status(self, status) -> None:  # type: ignore[override]
        log.info("vip.stub.save_status", user_id=status.user_id.value)

    async def get_scheduler_job(self, job_id):  # type: ignore[override]
        return None

    async def create_scheduler_job(self):  # type: ignore[override]
        from datetime import datetime, timezone

        from .models import JobId, SchedulerJob

        return SchedulerJob(id=1, timestamp=datetime.now(timezone.utc), done=False)

    async def complete_scheduler_job(self, job_id, users_processed: int) -> None:  # type: ignore[override]
        log.info("vip.stub.complete_job", job_id=job_id.value, users_processed=users_processed)

    async def get_all_user_ids(self, brand_id: BrandId):  # type: ignore[override]
        return []

    async def publish_tier_change(self, event) -> None:  # type: ignore[override]
        log.info("vip.stub.publish_tier_change", user_id=event.user_id.value)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config() -> AppConfig:
    return AppConfig(
        db=DbConfig(
            connection_uri=os.getenv("DB_URI", "postgresql+asyncpg://localhost/vip"),
            user=os.getenv("DB_USER", "vip"),
            password=os.getenv("DB_PASSWORD", ""),
            schema=os.getenv("DB_SCHEMA", "public"),
        ),
        kafka=KafkaConfig(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            group_id=os.getenv("KAFKA_GROUP_ID", "vip-rule-processor"),
            transactions_topic=os.getenv("KAFKA_TRANSACTIONS_TOPIC", "accounts-events"),
            commands_topic=os.getenv("KAFKA_COMMANDS_TOPIC", "vip-recalculate-commands"),
            events_topic=os.getenv("KAFKA_EVENTS_TOPIC", "vip-rule-updated"),
        ),
        scheduler=SchedulerConfig(
            enabled=os.getenv("SCHEDULER_ENABLED", "true").lower() == "true",
            brand_id=int(os.getenv("BRAND_ID", "1")),
            clock_interval_seconds=int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "600")),
            start_hour=int(os.getenv("SCHEDULER_START_HOUR", "0")),
            start_minute=int(os.getenv("SCHEDULER_START_MINUTE", "0")),
        ),
        http=HttpConfig(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8080")),
        ),
    )


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

_config = load_config()
_repo = StubVipRepository()
_evaluator = EvaluateUserStatus(repository=_repo)
_txn_processor = TransactionsEventProcessor(
    kafka_config=_config.kafka,
    brand_id=BrandId(value=_config.scheduler.brand_id),
    evaluator=_evaluator,
)
_cmd_processor = RecalculationCommandProcessor(
    kafka_config=_config.kafka,
    evaluator=_evaluator,
)
_scheduler = SchedulerFlow(
    scheduler_config=_config.scheduler,
    brand_id=BrandId(value=_config.scheduler.brand_id),
    repository=_repo,
    evaluator=_evaluator,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    log.info("vip_processor.starting", brand_id=_config.scheduler.brand_id)

    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(_txn_processor.run(), name="txn-consumer"),
        asyncio.create_task(_cmd_processor.run(), name="cmd-consumer"),
    ]
    if _config.scheduler.enabled:
        tasks.append(asyncio.create_task(_scheduler.run(), name="scheduler"))

    yield

    for task in tasks:
        task.cancel()

    log.info("vip_processor.stopped")


app = FastAPI(title="VIP Rule Processor", lifespan=lifespan)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.post("/users/{user_id}/recalculate")
async def recalculate_user(user_id: int, brand_id: int = 1) -> JSONResponse:
    """
    Trigger an immediate VIP tier recalculation for the given user.

    Returns the new tier name, or null if the player is not eligible.
    """
    from datetime import datetime, timezone

    command = RecalculateCommand(
        user_id=UserId(value=user_id),
        brand_id=BrandId(value=brand_id),
        timestamp=datetime.now(timezone.utc),
    )
    result = await _evaluator.evaluate(
        user_id=command.user_id,
        brand_id=command.brand_id,
        triggered_by="manual_api",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="User or rules not found")

    return JSONResponse({"result": result.__class__.__name__, "tier": getattr(result, "new_tier", None)})


@app.get("/rules")
async def list_rules(brand_id: int = 1) -> JSONResponse:
    """Return all active VIP rules for the given brand."""
    rules = await _repo.get_rules_for_brand(BrandId(value=brand_id))
    return JSONResponse({"rules": [r.model_dump() for r in rules]})


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}
