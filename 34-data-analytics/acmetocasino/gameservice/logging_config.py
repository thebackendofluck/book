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
gameservice.logging_config — Structured Logging Setup
======================================================

Configures ``structlog`` for JSON-structured logging with automatic
propagation of correlation context across all log records produced within a
single request lifecycle.

Key features
------------
* **JSON output** — every log entry is a machine-parseable JSON object,
  compatible with Loki, Elasticsearch, and CloudWatch Logs Insights.
* **Mandatory audit fields** — ``correlation_id``, ``player_id``,
  ``jurisdiction``, ``brand``, ``supplier``, ``round_id``, and ``timestamp``
  appear on every log record when set in the active context.
* **Context propagation via contextvars** — :class:`CorrelationContext` uses
  Python's :mod:`contextvars` module so context fields propagate correctly
  through async / threaded code without manual plumbing.
* **Stdlib integration** — :func:`configure_logging` installs a
  ``structlog`` processor chain on the standard-library ``logging`` module so
  that third-party libraries (SQLAlchemy, httpx, confluent-kafka) emit
  structured JSON as well.

Usage
-----
::

    # Application entry point (once per process)
    from acmetocasino.gameservice.logging_config import configure_logging
    configure_logging(service_name="acmetocasino-gameservice", level="INFO")

    # Per-request setup (FastAPI middleware / WSGI middleware)
    from acmetocasino.gameservice.logging_config import CorrelationContext
    with CorrelationContext(
        correlation_id="abc123",
        player_id="p-001",
        jurisdiction="UKGC",
        brand="brand-uk",
    ):
        log = get_logger(__name__)
        log.info("player_launched_game", game_id="book-of-dead")
        # → {"event": "player_launched_game", "correlation_id": "abc123", ...}

    # Module-level logger acquisition
    from acmetocasino.gameservice.logging_config import get_logger
    logger = get_logger(__name__)
    logger.warning("supplier_slow", latency_ms=1200, supplier="netent")
"""

from __future__ import annotations

import logging
import logging.config
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Iterator

# ---------------------------------------------------------------------------
# Context variable storage
# ---------------------------------------------------------------------------

_CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="")
_PLAYER_ID: ContextVar[str] = ContextVar("player_id", default="")
_JURISDICTION: ContextVar[str] = ContextVar("jurisdiction", default="")
_BRAND: ContextVar[str] = ContextVar("brand", default="")
_SUPPLIER: ContextVar[str] = ContextVar("supplier", default="")
_ROUND_ID: ContextVar[str] = ContextVar("round_id", default="")
_SESSION_ID: ContextVar[str] = ContextVar("session_id", default="")


# ---------------------------------------------------------------------------
# Structlog processor: inject context fields
# ---------------------------------------------------------------------------


def _inject_correlation_context(
    logger: Any,
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor that injects active context fields into every log record.

    Only non-empty context values are injected to keep log records clean when
    context is not set (e.g. during startup).

    Parameters
    ----------
    logger:
        Structlog logger instance (ignored).
    method:
        Log level method name (ignored).
    event_dict:
        Mutable event dictionary being processed.

    Returns
    -------
    dict[str, Any]
        The mutated event dictionary with context fields added.
    """
    if v := _CORRELATION_ID.get():
        event_dict.setdefault("correlation_id", v)
    if v := _PLAYER_ID.get():
        event_dict.setdefault("player_id", v)
    if v := _JURISDICTION.get():
        event_dict.setdefault("jurisdiction", v)
    if v := _BRAND.get():
        event_dict.setdefault("brand", v)
    if v := _SUPPLIER.get():
        event_dict.setdefault("supplier", v)
    if v := _ROUND_ID.get():
        event_dict.setdefault("round_id", v)
    if v := _SESSION_ID.get():
        event_dict.setdefault("session_id", v)
    return event_dict


# ---------------------------------------------------------------------------
# Public configuration function
# ---------------------------------------------------------------------------


def configure_logging(
    service_name: str = "acmetocasino-gameservice",
    level: str = "INFO",
    *,
    json_logs: bool = True,
    add_caller_info: bool = False,
) -> None:
    """Configure structlog + stdlib logging for the acmetocasino platform.

    Call this function **once** at process startup, before any loggers are
    acquired.  Repeated calls are safe (structlog is re-configured in place).

    Parameters
    ----------
    service_name:
        Logical name of the service, added to every log record as the
        ``service`` field.
    level:
        Minimum log level.  Accepts stdlib names: ``"DEBUG"``, ``"INFO"``,
        ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``.
    json_logs:
        When ``True`` (the default) render logs as JSON.  Set to ``False``
        for human-readable console output during local development.
    add_caller_info:
        When ``True`` include ``caller_file``, ``caller_function``, and
        ``caller_line`` in every log record.  Useful for debugging; too noisy
        for production.
    """
    try:
        import structlog  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "structlog is required for structured logging. "
            "Install it with: pip install structlog"
        ) from exc

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # -----------------------------------------------------------------------
    # Shared processors (applied to every log record, regardless of renderer)
    # -----------------------------------------------------------------------
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_correlation_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if add_caller_info:
        shared_processors.insert(0, structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ))

    # -----------------------------------------------------------------------
    # Stdlib logging configuration
    # -----------------------------------------------------------------------
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.dev.ConsoleRenderer() if not json_logs
                    else structlog.processors.JSONRenderer(),
                ],
                "foreign_pre_chain": shared_processors,
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "plain",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {
            "handlers": ["console"],
            "level": numeric_level,
        },
        "loggers": {
            # Silence noisy libraries at WARNING unless explicitly overridden.
            "confluent_kafka": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
            "httpx": {"level": "WARNING"},
        },
    })

    # -----------------------------------------------------------------------
    # Structlog configuration
    # -----------------------------------------------------------------------
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Attach service name to every record via contextvars
    structlog.contextvars.bind_contextvars(service=service_name)

    logging.getLogger(__name__).info(
        "Logging configured",
        extra={"service": service_name, "level": level, "json_logs": json_logs},
    )


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------


def get_logger(name: str) -> Any:
    """Return a structlog ``BoundLogger`` for *name*.

    The returned logger automatically includes all fields bound via
    :class:`CorrelationContext` or :func:`bind_context`.

    Parameters
    ----------
    name:
        Logger name — conventionally the module ``__name__``.

    Returns
    -------
    structlog.BoundLogger
        A structlog bound logger.  Falls back to a stdlib logger if structlog
        is not installed (e.g. in minimal test environments).

    Examples
    --------
    ::

        logger = get_logger(__name__)
        logger.info("round_settled", round_id="r-001", payout="12.50")
    """
    try:
        import structlog  # type: ignore[import-untyped]
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Context binding helpers
# ---------------------------------------------------------------------------


def bind_context(**fields: str) -> None:
    """Bind *fields* to the current structlog contextvars context.

    Bound fields appear on all subsequent log calls from the current
    async task or thread until :func:`clear_context` is called.

    Parameters
    ----------
    **fields:
        Arbitrary key/value pairs to bind.  Standard keys are
        ``correlation_id``, ``player_id``, ``jurisdiction``, ``brand``,
        ``supplier``, ``round_id``, ``session_id``.

    Examples
    --------
    ::

        bind_context(correlation_id="abc123", player_id="p-001")
    """
    # Update our ContextVars for the _inject_correlation_context processor.
    _setters = {
        "correlation_id": _CORRELATION_ID,
        "player_id": _PLAYER_ID,
        "jurisdiction": _JURISDICTION,
        "brand": _BRAND,
        "supplier": _SUPPLIER,
        "round_id": _ROUND_ID,
        "session_id": _SESSION_ID,
    }
    for key, value in fields.items():
        if key in _setters:
            _setters[key].set(value)

    # Also bind to structlog's own contextvars store so the stdlib
    # ProcessorFormatter picks them up.
    try:
        import structlog  # type: ignore[import-untyped]
        structlog.contextvars.bind_contextvars(**fields)
    except ImportError:
        pass


def clear_context() -> None:
    """Clear all bound context fields for the current async task or thread.

    Call this at the end of a request handler to prevent context leakage
    between requests in long-lived worker processes.
    """
    _CORRELATION_ID.set("")
    _PLAYER_ID.set("")
    _JURISDICTION.set("")
    _BRAND.set("")
    _SUPPLIER.set("")
    _ROUND_ID.set("")
    _SESSION_ID.set("")

    try:
        import structlog  # type: ignore[import-untyped]
        structlog.contextvars.clear_contextvars()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# CorrelationContext — context manager
# ---------------------------------------------------------------------------


class CorrelationContext:
    """Context manager that binds audit fields for the duration of a block.

    On exit the previous field values are restored (token-based rollback),
    so nested ``CorrelationContext`` blocks compose correctly.

    Parameters
    ----------
    correlation_id:
        Trace ID that spans the full request lifecycle.
    player_id:
        Operator-assigned player identifier.
    jurisdiction:
        Regulatory jurisdiction code.
    brand:
        White-label brand identifier.
    supplier:
        Game supplier identifier.
    round_id:
        Current round identifier (set when inside a round lifecycle).
    session_id:
        Game session identifier.

    Examples
    --------
    ::

        with CorrelationContext(
            correlation_id="abc123",
            player_id="p-001",
            jurisdiction="UKGC",
            brand="brand-uk",
            supplier="netent",
        ):
            logger.info("round_started", round_id="r-001")
    """

    def __init__(
        self,
        *,
        correlation_id: str = "",
        player_id: str = "",
        jurisdiction: str = "",
        brand: str = "",
        supplier: str = "",
        round_id: str = "",
        session_id: str = "",
    ) -> None:
        self._fields: dict[str, str] = {}
        if correlation_id:
            self._fields["correlation_id"] = correlation_id
        if player_id:
            self._fields["player_id"] = player_id
        if jurisdiction:
            self._fields["jurisdiction"] = jurisdiction
        if brand:
            self._fields["brand"] = brand
        if supplier:
            self._fields["supplier"] = supplier
        if round_id:
            self._fields["round_id"] = round_id
        if session_id:
            self._fields["session_id"] = session_id

        self._tokens: list[Any] = []

    def __enter__(self) -> CorrelationContext:
        _var_map = {
            "correlation_id": _CORRELATION_ID,
            "player_id": _PLAYER_ID,
            "jurisdiction": _JURISDICTION,
            "brand": _BRAND,
            "supplier": _SUPPLIER,
            "round_id": _ROUND_ID,
            "session_id": _SESSION_ID,
        }
        for key, value in self._fields.items():
            if key in _var_map:
                token = _var_map[key].set(value)
                self._tokens.append((key, _var_map[key], token))

        try:
            import structlog  # type: ignore[import-untyped]
            structlog.contextvars.bind_contextvars(**self._fields)
        except ImportError:
            pass

        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        for _key, var, token in reversed(self._tokens):
            var.reset(token)

        try:
            import structlog  # type: ignore[import-untyped]
            structlog.contextvars.unbind_contextvars(*self._fields.keys())
        except ImportError:
            pass


@contextmanager
def correlation_scope(
    *,
    correlation_id: str = "",
    player_id: str = "",
    jurisdiction: str = "",
    brand: str = "",
    supplier: str = "",
    round_id: str = "",
    session_id: str = "",
) -> Generator[None, None, None]:
    """Async-friendly generator-based alternative to :class:`CorrelationContext`.

    Useful in ``async with`` or ``async for`` contexts where a class-based
    context manager would require ``__aenter__``/``__aexit__``.

    Examples
    --------
    ::

        async with correlation_scope(
            correlation_id="abc123",
            player_id="p-001",
            jurisdiction="UKGC",
        ):
            await process_round(...)
    """
    ctx = CorrelationContext(
        correlation_id=correlation_id,
        player_id=player_id,
        jurisdiction=jurisdiction,
        brand=brand,
        supplier=supplier,
        round_id=round_id,
        session_id=session_id,
    )
    with ctx:
        yield


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "configure_logging",
    "get_logger",
    "bind_context",
    "clear_context",
    "CorrelationContext",
    "correlation_scope",
]
