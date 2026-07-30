# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Internal Alerts Service — FastAPI + async entrypoint
# Source: Production casino platform (sanitized)
# Chapter 35 - Incident Management
#
# Starts three concurrent tasks:
#   1. FastAPI HTTP server (health / admin routes)
#   2. Kafka consumer (persists incoming alert messages)
#   3. Outbox service (dispatches pending alerts via mailer)
#
# Run:
#   uvicorn main:app --host 0.0.0.0 --port 8090
#
# Environment variables:
#   DATABASE_URL              - PostgreSQL DSN
#   KAFKA_BOOTSTRAP_SERVERS   - Kafka brokers
#   KAFKA_ALERT_TOPIC         - Source topic name
#   MAILER_BASE_URL           - Mailer service base URL
#   MAILER_ALERTS_PATH        - Mailer alerts endpoint path
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from kafka_consumer import KafkaAlertConsumer
from models import AlertStatus
from outbox_service import MailerClient, OutboxService
from repository import AlertRepository, AlertTypeRepository, EmailAddressRepository

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
)
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL      = os.getenv("DATABASE_URL", "postgresql://localhost/internal_alerts")
MAILER_BASE_URL   = os.getenv("MAILER_BASE_URL", "http://mailer:8091")
MAILER_ALERTS_PATH = os.getenv("MAILER_ALERTS_PATH", "/api/send")
PORT              = int(os.getenv("PORT", "8090"))

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

alert_repo        = AlertRepository(DATABASE_URL)
alert_type_repo   = AlertTypeRepository(DATABASE_URL)
email_addr_repo   = EmailAddressRepository(DATABASE_URL)
mailer_client     = MailerClient(MAILER_BASE_URL, MAILER_ALERTS_PATH)

outbox_service    = OutboxService(alert_repo, alert_type_repo, email_addr_repo, mailer_client)
kafka_consumer    = KafkaAlertConsumer(alert_repo)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Kafka consumer in a background daemon thread (it is blocking)
    t = threading.Thread(target=kafka_consumer.run_forever, daemon=True)
    t.start()
    log.info("kafka_consumer_started")

    # Schedule outbox polling as an asyncio task
    asyncio.create_task(outbox_service.run_forever())
    log.info("outbox_service_started")
    yield


app = FastAPI(title="Internal Alerts Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "UP", "service": "internal-alerts"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
