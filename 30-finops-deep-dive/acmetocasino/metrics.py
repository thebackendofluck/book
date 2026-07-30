# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Prometheus metrics definitions for the AcmeToCasino platform.

All counters, gauges, and histograms used across the application
are defined here for centralized management.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ---------- HTTP ----------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ---------- Players ----------

active_players = Gauge(
    "active_players",  # Gauge: no _total suffix (reserved for counters)
    "Number of active player accounts",
)

# ---------- Wallet ----------

wallet_events_total = Counter(
    "wallet_events_total",
    "Total wallet events by type",
    ["event_type"],
)

# ---------- GAL ----------

game_rounds_total = Counter(
    "game_rounds_total",
    "Total game rounds played",
    ["game_slug"],
)

game_rounds_bet_amount = Histogram(
    "game_rounds_bet_amount",
    "Distribution of bet amounts per game",
    ["game_slug"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000),
)

rng_calls_total = Counter(
    "rng_calls_total",
    "Total CSPRNG calls",
)

# ---------- Compliance ----------

aml_alerts_total = Counter(
    "aml_alerts_total",
    "Total AML alerts created",
    ["alert_type", "severity"],
)

kyc_checks_total = Counter(
    "kyc_checks_total",
    "Total KYC checks by status",
    ["status"],
)

# ---------- WebSocket ----------

websocket_connections = Gauge(
    "websocket_connections",
    "Current active WebSocket connections",
)

# ---------- Redis Pub/Sub ----------

redis_pubsub_messages_total = Counter(
    "redis_pubsub_messages_total",
    "Total Redis Pub/Sub messages published",
    ["channel"],
)


def get_metrics() -> bytes:
    """Return all Prometheus metrics in text exposition format."""
    return generate_latest()


def get_metrics_content_type() -> str:
    """Return the correct Content-Type header for Prometheus scraping."""
    return CONTENT_TYPE_LATEST
