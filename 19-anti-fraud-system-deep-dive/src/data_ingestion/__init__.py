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
Fraud Detection Data Ingestion Service

This package provides real-time data ingestion capabilities for the fraud detection system,
handling various event types from casino operations including transactions, user events,
and game events.
"""

__version__ = "1.0.0"
__author__ = "Fraud Detection Team"
__description__ = "Real-time data ingestion service for fraud detection"

from .app import app  # ty:ignore[unresolved-import]
from .config import settings  # ty:ignore[unresolved-import]
from .kafka_producer import KafkaEventProducer, get_kafka_producer  # ty:ignore[unresolved-import]
from .metrics import MetricsCollector  # ty:ignore[unresolved-import]
from .validation import DataValidator  # ty:ignore[unresolved-import]
from .enrichment import DataEnricher  # ty:ignore[unresolved-import]

__all__ = [
    "app",
    "settings",
    "KafkaEventProducer",
    "get_kafka_producer",
    "MetricsCollector",
    "DataValidator",
    "DataEnricher"
]