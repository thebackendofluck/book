# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Structured JSON logging with correlation IDs.

Every log entry includes:
  - timestamp (ISO 8601)
  - level
  - service (app name)
  - module (logger name)
  - action (log message)
  - correlation_id (per-request UUID)
  - player_id (when available)
  - details (extra context)
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import settings  # ty: ignore[unresolved-import]

# Context variable for per-request correlation ID
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")
player_id_var: ContextVar[str] = ContextVar("player_id", default="-")


def new_correlation_id() -> str:
    """Generate a new correlation ID for a request."""
    return str(uuid.uuid4())


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": settings.APP_NAME,
            "module": record.name,
            "action": record.getMessage(),
            "correlation_id": correlation_id_var.get("-"),
            "player_id": player_id_var.get("-"),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "details"):
            entry["details"] = record.details
        return json.dumps(entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter that still includes correlation ID."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cid = correlation_id_var.get("-")
        prefix = f"{ts} [{record.levelname}] [{cid[:8]}] {record.name}:"
        msg = f"{prefix} {record.getMessage()}"
        if record.exc_info and record.exc_info[1]:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(json_output: bool = False) -> None:
    """
    Configure root logger with structured or console formatting.

    Args:
        json_output: If True, emit JSON lines. If False, use readable console format.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)

    # Remove any existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    root.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
