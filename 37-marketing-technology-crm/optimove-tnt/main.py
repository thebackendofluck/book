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
Optimove TnT integration -- FastAPI + background Kafka consumers.

Exposes a health/readiness endpoint and runs concurrent Kafka consumers
that forward player events to the Optimove Track and Trigger API.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from .models import (
    AppConfig,
    HttpConfig,
    KafkaConfig,
    KafkaConsumerConfig,
    OpsGenieConfig,
)
from .service import Application

log = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
USERS_TOPIC = os.getenv("KAFKA_USERS_TOPIC", "user-events")
TRANSACTIONS_TOPIC = os.getenv("KAFKA_TRANSACTIONS_TOPIC", "transaction-events")


def build_config() -> AppConfig:
    return AppConfig(
        kafka=KafkaConfig(
            bootstrap=KAFKA_BOOTSTRAP,
            consumer_users=KafkaConsumerConfig(
                topic=USERS_TOPIC,
                error_topic=f"{USERS_TOPIC}-error",
            ),
            consumer_transactions=KafkaConsumerConfig(
                topic=TRANSACTIONS_TOPIC,
                error_topic=f"{TRANSACTIONS_TOPIC}-error",
            ),
        ),
        ops_genie=OpsGenieConfig(
            api_key=os.getenv("OPSGENIE_API_KEY", ""),
            enabled=os.getenv("OPSGENIE_ENABLED", "false").lower() == "true",
        ),
    )


_app_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_task
    log.info("optimove_tnt.starting")
    config = build_config()
    application = Application(config)
    _app_task = asyncio.create_task(application.start())
    yield
    if _app_task:
        _app_task.cancel()
    log.info("optimove_tnt.stopped")


app = FastAPI(title="Optimove TnT Integration", lifespan=lifespan)


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readiness() -> dict[str, str]:
    return {"status": "ready"}
