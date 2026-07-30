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
platform.id_factory — ID Generation Utilities
==============================================

Consistent, domain-appropriate identifiers across the platform.

ID types
--------
``uuid7``
    Version 7 UUID — time-ordered, globally unique, sortable.  Uses
    millisecond-precision Unix timestamp in the high bits and random data
    in the low bits.  This makes IDs:

    * **Sortable** by creation time without a separate ``created_at`` column.
    * **DB-friendly** — monotonically increasing IDs cause far less B-tree
      page fragmentation than random UUIDs (v4).
    * **Globally unique** — safe for distributed generation without
      coordination.

    UUIDv7 is specified in RFC 9562 (April 2024) and is natively supported
    in Python 3.13+ via ``uuid.uuid7()``.  For earlier Python versions we
    provide a compatible implementation.

``short_id``
    A compact, URL-safe, human-readable identifier derived from a UUID7.
    Encoded in Base32 (Crockford variant) without padding to produce an
    alphanumeric string.  Suitable for:

    * URL path segments (e.g. ``/rounds/01j2k3m4n5p6q7r8s9t0``)
    * Display in customer emails and support tickets.
    * QR code payloads.

    Short IDs are **not** guaranteed to be universally unique on their own —
    they are just a compact representation of a full UUID7.

``correlation_id``
    Alias for UUID7, used as a trace identifier that spans multiple systems.

Example::

    factory = IdFactory()
    uid = factory.uuid7()          # "01918fb2-7b3c-7000-8000-abc123def456"
    short = factory.short_id()     # "01GRHV3FMSDMKX8AK5BT9EFYPJ"
    cid = factory.correlation_id() # same format as uuid7
"""

from __future__ import annotations

import os
import struct
import time
import uuid
from base64 import b32encode


# ---------------------------------------------------------------------------
# UUID7 implementation
# ---------------------------------------------------------------------------


def _uuid7() -> uuid.UUID:
    """Generate a UUID version 7 (time-ordered).

    Layout (128 bits):
    ┌──────────────────────────┬───┬───┬────────────────────────────┐
    │  unix_ts_ms (48 bits)    │ver│var│  random bits (74 bits)     │
    └──────────────────────────┴───┴───┴────────────────────────────┘

    This is compatible with the RFC 9562 specification.  Python 3.13+
    provides ``uuid.uuid7()`` natively; this fallback implementation is
    used on earlier versions.
    """
    # Try native implementation first (Python 3.13+).
    native = getattr(uuid, "uuid7", None)
    if native is not None:
        return native()

    # Fallback: manual implementation.
    ts_ms = int(time.time() * 1000)
    # 48 bits of millisecond timestamp
    ts_high = (ts_ms & 0xFFFFFFFFFFFF) << 80
    # version nibble = 0b0111 (7)
    version = 0x7 << 76
    # 74 bits of random data
    rand_bytes = os.urandom(10)
    rand_int = int.from_bytes(rand_bytes, "big") & 0x3FFFFFFFFFFFFFFFFFFFF
    # variant bits: 10xxxxxx
    variant = 0b10 << 62

    uuid_int = ts_high | version | rand_int | variant
    return uuid.UUID(int=uuid_int)


# ---------------------------------------------------------------------------
# IdFactory
# ---------------------------------------------------------------------------


class IdFactory:
    """Generates platform identifiers.

    All methods return new, unique values on each call.  The class holds no
    state and is safe to use from multiple threads.

    Design note: a class is used rather than bare module-level functions so
    that the factory can be injected as a dependency and replaced with a
    deterministic stub in tests (e.g. a factory that returns sequential IDs
    for reproducible assertions).
    """

    def uuid7(self) -> str:
        """Return a new UUID7 as a lowercase hyphenated string.

        Returns
        -------
        str
            Example: ``"01918fb2-7b3c-7000-8000-abc123def456"``
        """
        return str(_uuid7())

    def short_id(self) -> str:
        """Return a compact, URL-safe Base32 representation of a UUID7.

        The ID is derived from a UUID7 to retain time-ordering while being
        more human-friendly than the full hyphenated form.

        Returns
        -------
        str
            26-character uppercase alphanumeric string (Crockford Base32
            without padding).

        Example: ``"01GRHV3FMSDMKX8AK5BT9EFYPJ"``
        """
        uid = _uuid7()
        raw = uid.bytes  # 16 bytes
        encoded = b32encode(raw).decode("ascii").rstrip("=")
        return encoded

    def correlation_id(self) -> str:
        """Return a new correlation ID suitable for distributed tracing.

        Identical format to :meth:`uuid7`.  Provided as a semantically
        distinct method so call-sites are self-documenting.

        Returns
        -------
        str
            UUID7 string.
        """
        return self.uuid7()

    def player_id(self) -> str:
        """Return a new player identifier.

        Prefixed with ``"p-"`` so IDs can be distinguished by type in logs.

        Returns
        -------
        str
            Example: ``"p-01918fb2-7b3c-7000-8000-abc123def456"``
        """
        return f"p-{self.uuid7()}"

    def session_id(self) -> str:
        """Return a new session identifier.

        Prefixed with ``"s-"``.

        Returns
        -------
        str
        """
        return f"s-{self.uuid7()}"

    def round_id(self) -> str:
        """Return a new round identifier.

        Prefixed with ``"r-"``.

        Returns
        -------
        str
        """
        return f"r-{self.uuid7()}"

    def transaction_id(self) -> str:
        """Return a new ledger transaction identifier.

        Prefixed with ``"tx-"``.

        Returns
        -------
        str
        """
        return f"tx-{self.uuid7()}"


# Module-level singleton for convenience.
_default_factory = IdFactory()

uuid7 = _default_factory.uuid7
short_id = _default_factory.short_id
correlation_id = _default_factory.correlation_id


__all__ = [
    "IdFactory",
    "correlation_id",
    "short_id",
    "uuid7",
]
