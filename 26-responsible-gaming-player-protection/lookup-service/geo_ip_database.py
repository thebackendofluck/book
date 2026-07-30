# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""MaxMind GeoIP2 wrapper with lazy loading and CRC32 checksum reload.

This is the Python port of the production Scala GeoIpDatabase referenced in
chapter 26, section "Lookup Service: GeoIP and Jurisdiction Reference Data".
It deliberately depends only on the `geoip2` package when a real database
is supplied; a stub resolver is provided for unit tests and environments
where the MaxMind database has not been downloaded yet.

Behaviour matched from the original Scala:

1. **Lazy loading** -- the `.mmdb` file is not opened until the first
   `country_code_for` call. Importing this module has no filesystem cost.
2. **CRC32 checksum** -- on every Nth query (default 1,000) the file CRC
   is compared against the last loaded value. If the file has been
   replaced by the update job, the in-memory reader is rebuilt so that
   the next query returns data from the new database. This is how the
   production service picks up weekly MaxMind updates without a restart.
3. **Country code resolution** -- accepts either a string IP or a
   pre-validated `ipaddress.IPv4Address`/`IPv6Address` object and
   returns the ISO 3166-1 alpha-2 country code, or `None` if the IP is
   unknown (RFC 1918, loopback, or simply missing from the database).

The module exposes a `GeoIpDatabase` class and a `null_geo_ip_database()`
factory that returns an instance backed by an empty in-memory map --
useful for tests and for running the rest of the service without a
MaxMind licence key.
"""

from __future__ import annotations

import ipaddress
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

try:
    import geoip2.database  # type: ignore[import]
    import geoip2.errors  # type: ignore[import]
    _GEOIP2_AVAILABLE = True
except ImportError:
    _GEOIP2_AVAILABLE = False


class _Reader(Protocol):
    """Minimal protocol implemented by both geoip2.database.Reader and
    the in-memory stub used in tests. Only the single method we call
    from this module is required.
    """

    def country(self, ip: str) -> object:  # pragma: no cover - protocol only
        ...

    def close(self) -> None:  # pragma: no cover
        ...


@dataclass
class _LoadedDatabase:
    """Snapshot of a loaded GeoIP database and the file CRC it was loaded from."""

    reader: _Reader
    crc32: int
    path: Path


class GeoIpDatabase:
    """Thread-safe wrapper around a MaxMind GeoIP2 country database.

    Parameters
    ----------
    database_path:
        Absolute path to a `.mmdb` file. The file does not need to exist
        at construction time; the first call to `country_code_for` will
        attempt to load it. A non-existent file will cause queries to
        return `None` (failing open is preferred over failing closed for
        geo-blocking because a hard failure would lock every player out).
    reload_every:
        How often the CRC check runs, expressed in number of queries.
        Defaults to 1,000 which keeps the CRC overhead below 0.1% even
        on a busy registration endpoint. Set to 0 to disable CRC
        reloading entirely (useful for unit tests where the file is
        static).
    reader_factory:
        Optional override for the `_Reader` constructor. Production code
        leaves this as `None` so that `geoip2.database.Reader` is used;
        tests inject a stub here to avoid touching the filesystem.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        reload_every: int = 1000,
        reader_factory: "object | None" = None,
    ) -> None:
        self._path = Path(database_path)
        self._reload_every = reload_every
        self._reader_factory = reader_factory
        self._loaded: _LoadedDatabase | None = None
        self._query_count = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def country_code_for(
        self, ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> str | None:
        """Return the ISO 3166-1 alpha-2 country code for the given IP.

        Returns `None` when the address is private, loopback, missing
        from the database, or the database itself cannot be loaded.
        Never raises for a non-existent database file -- the caller is
        expected to treat `None` as "unknown jurisdiction".
        """
        ip_str = self._normalize_ip(ip)
        if ip_str is None:
            return None

        with self._lock:
            self._query_count += 1
            if self._should_reload():
                self._reload()
            if self._loaded is None:
                return None
            reader = self._loaded.reader

        try:
            response = reader.country(ip_str)
        except Exception:  # geoip2 raises AddressNotFoundError; stub may raise KeyError
            return None

        iso = getattr(getattr(response, "country", None), "iso_code", None)
        if iso is None:
            return None
        return str(iso).upper()

    def close(self) -> None:
        """Release the underlying file handle if a database is loaded."""
        with self._lock:
            if self._loaded is not None:
                try:
                    self._loaded.reader.close()
                except Exception:
                    pass
                self._loaded = None

    def __enter__(self) -> "GeoIpDatabase":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _normalize_ip(
        self, ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> str | None:
        """Validate and return the string form of an IP, or None if private."""
        try:
            if isinstance(ip, str):
                addr = ipaddress.ip_address(ip)
            else:
                addr = ip
        except ValueError:
            return None
        if addr.is_private or addr.is_loopback or addr.is_multicast or addr.is_unspecified:
            return None
        return str(addr)

    def _should_reload(self) -> bool:
        if self._loaded is None:
            return True
        if self._reload_every <= 0:
            return False
        return (self._query_count % self._reload_every) == 0

    def _reload(self) -> None:
        """Load the database if the file is present and the CRC has changed.

        Holds the instance lock during the entire reload so that a single
        file update doesn't produce a torn read.
        """
        if not self._path.exists():
            # Reset so the next call tries again; do not wipe an existing
            # reader when a transient filesystem error hides the file.
            if self._loaded is None:
                return
            # If the file has been removed, log-and-ignore: keep serving
            # from the last loaded reader. A real logger would go here;
            # we stay print-free so unit tests can capture stdout cleanly.
            return

        crc = self._crc32_of(self._path)
        if self._loaded is not None and self._loaded.crc32 == crc:
            return

        # Build a fresh reader. The resulting object is either a
        # user-supplied test stub or a `geoip2.database.Reader`; both
        # expose the `.country()` and `.close()` methods that the
        # `_Reader` protocol requires, so the cast is safe.
        raw_reader: object
        if self._reader_factory is not None:
            raw_reader = self._reader_factory(str(self._path))  # type: ignore[operator]
        elif _GEOIP2_AVAILABLE:
            raw_reader = geoip2.database.Reader(str(self._path))
        else:
            # No factory and no geoip2 installed: treat as "no database"
            return

        reader = cast("_Reader", raw_reader)
        if self._loaded is not None:
            try:
                self._loaded.reader.close()
            except Exception:
                pass
        self._loaded = _LoadedDatabase(reader=reader, crc32=crc, path=self._path)

    @staticmethod
    def _crc32_of(path: Path) -> int:
        """Return the CRC32 of a file, read in 1 MB chunks to avoid slurping
        the whole MaxMind database into memory.
        """
        crc = 0
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
        return crc


# ---------------------------------------------------------------------------
# Null / stub implementations
# ---------------------------------------------------------------------------


@dataclass
class _StubCountry:
    iso_code: str | None


@dataclass
class _StubCountryResponse:
    country: _StubCountry


class _StubReader:
    """In-memory reader that maps specific IPs to country codes.

    Used by `null_geo_ip_database` and by the unit tests. Behaves like
    geoip2.database.Reader.country() but never touches disk.
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def country(self, ip: str) -> _StubCountryResponse:
        code = self._mapping.get(ip)
        if code is None:
            raise KeyError(ip)
        return _StubCountryResponse(country=_StubCountry(iso_code=code))

    def close(self) -> None:
        return


def null_geo_ip_database() -> GeoIpDatabase:
    """Return a GeoIpDatabase that resolves every IP to None.

    Useful for tests that exercise the rest of the lookup-service stack
    without needing a real MaxMind database on disk.
    """
    db = GeoIpDatabase(
        database_path="/nonexistent",
        reload_every=0,
        reader_factory=lambda _path: _StubReader(),
    )
    # Force the null reader in place so queries don't hit the "no file" early return.
    db._loaded = _LoadedDatabase(reader=_StubReader(), crc32=0, path=Path("/dev/null"))
    return db


def in_memory_geo_ip_database(mapping: dict[str, str]) -> GeoIpDatabase:
    """Return a GeoIpDatabase backed by a fixed IP-to-country map.

    Test fixtures use this instead of stubbing `geoip2` imports.
    """
    db = GeoIpDatabase(
        database_path="/nonexistent",
        reload_every=0,
        reader_factory=lambda _path: _StubReader(mapping),
    )
    db._loaded = _LoadedDatabase(
        reader=_StubReader(mapping), crc32=0, path=Path("/dev/null")
    )
    return db
