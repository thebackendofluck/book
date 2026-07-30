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
platform.database — Database Abstraction
=========================================

Provides a thin, async-friendly database interface that decouples domain
services from the underlying storage engine.

Architecture
------------
The ``DatabaseAdapter`` Protocol defines the operations that the platform
core needs:

* **Fetch** — retrieve a single record by primary key or query predicate.
* **Fetch many** — retrieve multiple matching records.
* **Upsert** — create or update a record atomically.
* **Delete** — soft or hard delete.
* **Transaction** — execute a callable within a database transaction.

In production, implement this Protocol on top of an async SQLAlchemy session
factory (e.g. ``AsyncSession``) or any other async ORM/driver.

The :class:`InMemoryDatabaseAdapter` provided here is suitable for unit tests
and local development.  It stores records as plain Python dicts in-memory,
indexed by table name and primary key.

Async design note
-----------------
Although the Protocol signatures are defined as ``async def``, the in-memory
implementation uses synchronous code wrapped in coroutines.  Real production
adapters should use actual async I/O (``asyncpg``, ``aiomysql``, etc.) to
benefit from non-blocking database access in an async web framework.

Example::

    adapter = InMemoryDatabaseAdapter()
    await adapter.upsert("players", key="p-1", record={"name": "Alice"})
    player = await adapter.fetch("players", key="p-1")
    assert player == {"name": "Alice"}
"""

from __future__ import annotations

import threading
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

# Type alias for a table: dict[primary_key_str, record_dict]
_Table = dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DatabaseAdapter(Protocol):
    """Structural contract for async database back-ends.

    All methods are ``async`` to support non-blocking I/O in production.
    The in-memory implementation satisfies the interface using synchronous
    in-memory operations wrapped in coroutines (acceptable for tests, where
    the event loop overhead is negligible).
    """

    async def fetch(
        self,
        table: str,
        key: str,
    ) -> dict[str, Any] | None:
        """Fetch a single record by primary key.

        Parameters
        ----------
        table:
            Logical table name.
        key:
            Primary key value (string).

        Returns
        -------
        dict or None
            The record, or ``None`` if no record exists for *key*.
        """
        ...

    async def fetch_many(
        self,
        table: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch multiple records from *table*, optionally filtered.

        Parameters
        ----------
        table:
            Logical table name.
        predicate:
            Optional callable that returns ``True`` for records to include.
            Defaults to returning all records.
        limit:
            Maximum number of records to return.

        Returns
        -------
        list[dict]
            Matching records.
        """
        ...

    async def upsert(
        self,
        table: str,
        key: str,
        record: dict[str, Any],
    ) -> None:
        """Create or update a record.

        Parameters
        ----------
        table:
            Logical table name.
        key:
            Primary key value.
        record:
            The full record to store.  Replaces any existing record for
            *key* entirely (no partial updates).
        """
        ...

    async def delete(
        self,
        table: str,
        key: str,
    ) -> bool:
        """Remove a record.

        Parameters
        ----------
        table:
            Logical table name.
        key:
            Primary key of the record to remove.

        Returns
        -------
        bool
            ``True`` if the record existed and was removed; ``False`` if
            no record for *key* was found.
        """
        ...

    async def count(self, table: str) -> int:
        """Return the number of records in *table*."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryDatabaseAdapter:
    """Thread-safe in-memory database adapter for testing.

    State is stored in a nested dict:
    ``{ table_name: { primary_key: record_dict } }``

    All records are deep-copied on read and write to prevent accidental
    mutation of stored state.

    This adapter is NOT suitable for production.
    """

    def __init__(self) -> None:
        self._tables: dict[str, _Table] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    async def fetch(
        self,
        table: str,
        key: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._tables.get(table, {}).get(key)
            return deepcopy(record) if record is not None else None

    async def fetch_many(
        self,
        table: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._tables.get(table, {}).values())
        if predicate is not None:
            rows = [r for r in rows if predicate(r)]
        return [deepcopy(r) for r in rows[:limit]]

    async def upsert(
        self,
        table: str,
        key: str,
        record: dict[str, Any],
    ) -> None:
        with self._lock:
            if table not in self._tables:
                self._tables[table] = {}
            self._tables[table][key] = deepcopy(record)

    async def delete(
        self,
        table: str,
        key: str,
    ) -> bool:
        with self._lock:
            table_data = self._tables.get(table, {})
            if key in table_data:
                del table_data[key]
                return True
            return False

    async def count(self, table: str) -> int:
        with self._lock:
            return len(self._tables.get(table, {}))

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self, table: str | None = None) -> None:
        """Clear all records (from *table*, or all tables if ``None``).

        Useful for resetting state between test cases.

        Parameters
        ----------
        table:
            Specific table to clear, or ``None`` to clear everything.
        """
        with self._lock:
            if table is not None:
                self._tables.pop(table, None)
            else:
                self._tables.clear()

    def seed(self, table: str, key: str, record: dict[str, Any]) -> None:
        """Synchronously insert a record for test setup.

        Avoids the need to ``await`` in ``setUp`` / fixture code.

        Parameters
        ----------
        table:
            Logical table name.
        key:
            Primary key.
        record:
            The record to insert.
        """
        with self._lock:
            if table not in self._tables:
                self._tables[table] = {}
            self._tables[table][key] = deepcopy(record)

    def table_names(self) -> list[str]:
        """Return the list of table names that have at least one record."""
        with self._lock:
            return list(self._tables.keys())


__all__ = ["DatabaseAdapter", "InMemoryDatabaseAdapter"]
