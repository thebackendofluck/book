# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# wallet_service/startup.py
"""Primary-cluster ownership for the wallet service.

The right to write player balances is a *lease with an expiry*, not a boolean
decided once at startup.

The previous version of this module called ``claim_primary_cluster()``, which
took ``pg_try_advisory_lock(12345)``, and then closed the cursor and the
connection in the same function. Advisory locks taken that way are session
scoped, so the lock was released before ``claim_primary_or_exit()`` even
returned. The mutual exclusion it looked like it provided did not exist. After a
switchover the old colour is only cordoned and drained and its pods live on for
up to an hour, so any wallet pod restarting in that window found the lock free,
declared itself primary, cleared ``WALLET_READ_ONLY`` and began writing player
balances into the same PostgreSQL the live colour was writing to.

What replaces it (see ``sql/cluster_registry.sql`` for the SQL side):

* The lease belongs to a cluster **colour**, so all three wallet replicas of the
  active colour can write, and none of the standby colour's replicas can.
* Ownership is bounded in time. ``renew_primary_lease`` is the heartbeat. If this
  process cannot renew before its own deadline passes, it stops being primary and
  :func:`wallet_is_read_only` starts returning ``True``. Losing the database
  connection therefore means losing primary status, never silently continuing to
  write.
* Every handover advances a monotonic epoch. :meth:`PrimaryLease.fence` asserts
  the colour and epoch inside the writing transaction, so a request that was
  in flight when this pod was demoted is aborted by PostgreSQL instead of
  committing a stale balance.

Connection ownership, explicitly: the lease holds one dedicated psycopg2
connection that is never handed to the application's pool and never used for
application queries. Before :meth:`PrimaryLease.start` returns, the calling
thread is the only user. Afterwards the renewer thread owns it; every access
goes through ``self._conn_lock``, and only ``close()``/``release()`` touch it
from outside, after stopping the renewer. TCP keepalives are set aggressively so
a silently dead peer surfaces in seconds rather than at the OS default of hours.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import socket
import threading
import time
from typing import Any, Callable

import psycopg2

LOG = logging.getLogger("wallet.primary_lease")

# Lease lifetime. The switchover pauses for LEASE_TTL_SECONDS plus a margin
# between releasing the old colour and claiming the new one, so keep this short
# enough that the handover window stays acceptable and long enough that a couple
# of consecutive renewal failures do not demote a healthy pod.
LEASE_TTL_SECONDS = int(os.environ.get("WALLET_LEASE_TTL_SECONDS", "20"))
RENEW_INTERVAL_SECONDS = int(os.environ.get("WALLET_LEASE_RENEW_SECONDS", "5"))

# Stop writing this far before the lease actually expires, to cover clock skew
# and the round trip of the write itself. The database fence is the backstop;
# this margin keeps us from relying on it in normal operation.
SAFETY_MARGIN_SECONDS = float(os.environ.get("WALLET_LEASE_MARGIN_SECONDS", "2"))

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class NotPrimary(RuntimeError):
    """Raised when a write is attempted without a live primary lease."""


class PrimaryLease:
    """A time-bounded, fenced claim on the right to write player balances."""

    def __init__(
        self,
        dsn: str,
        cluster_color: str,
        cluster_node: str | None = None,
        ttl_seconds: int = LEASE_TTL_SECONDS,
        renew_interval_seconds: int = RENEW_INTERVAL_SECONDS,
        safety_margin_seconds: float = SAFETY_MARGIN_SECONDS,
        on_lost: Callable[[str], None] | None = None,
    ) -> None:
        if renew_interval_seconds + safety_margin_seconds >= ttl_seconds:
            raise ValueError(
                "renew interval plus safety margin must be shorter than the TTL, "
                f"got {renew_interval_seconds}s + {safety_margin_seconds}s >= {ttl_seconds}s"
            )

        self._dsn = dsn
        self._color = cluster_color
        self._node = cluster_node or socket.gethostname()
        self._ttl = ttl_seconds
        self._renew_interval = renew_interval_seconds
        self._margin = safety_margin_seconds
        self._on_lost = on_lost or _default_on_lost

        self._state_lock = threading.Lock()
        self._conn_lock = threading.Lock()
        self._conn: Any | None = None
        self._epoch: int | None = None
        self._deadline = 0.0
        self._lost_reason: str | None = None
        self._stop = threading.Event()
        self._renewer: threading.Thread | None = None

    # ── public API ───────────────────────────────────────────────────────────

    @property
    def cluster_color(self) -> str:
        return self._color

    def is_primary(self) -> bool:
        """True only while this pod holds a lease that has not run out."""
        with self._state_lock:
            return self._epoch is not None and time.monotonic() < self._deadline - self._margin

    def epoch(self) -> int:
        """The fencing token for the current lease, or raise :class:`NotPrimary`."""
        with self._state_lock:
            if self._epoch is None or time.monotonic() >= self._deadline - self._margin:
                raise NotPrimary(
                    f"{self._color} does not hold the primary lease"
                    + (f" ({self._lost_reason})" if self._lost_reason else "")
                )
            return self._epoch

    def fence(self, cur: Any) -> None:
        """First statement of every balance-mutating transaction.

        Aborts the transaction from inside PostgreSQL if this colour and epoch no
        longer own the lease. ``cur`` must be a cursor on the connection that
        will perform the write, so the fence and the write share a transaction.
        """
        cur.execute("SELECT assert_primary_lease(%s, %s)", (self._color, self.epoch()))

    def try_claim(self) -> bool:
        """Attempt to become primary. False means another colour holds a live lease."""
        started = time.monotonic()
        row = self._query(
            "SELECT acquired, lease_epoch, lease_expires_at, holder_color "
            "FROM claim_primary_cluster(%s, %s, %s)",
            (self._color, self._node, self._ttl),
        )
        if row is None:
            raise RuntimeError("claim_primary_cluster returned no row")

        acquired, epoch, expires_at, holder = row
        if not acquired:
            LOG.warning(
                "[STARTUP] %s cannot claim primary: %s holds the lease (epoch %s) until %s",
                self._color, holder, epoch, expires_at,
            )
            return False

        with self._state_lock:
            self._epoch = int(epoch)
            self._deadline = started + self._ttl
            self._lost_reason = None
        LOG.info(
            "[STARTUP] Claimed primary status for %s cluster (epoch %s, node %s)",
            self._color, epoch, self._node,
        )
        return True

    def start(self) -> None:
        """Start the renewer. From here on the renewer thread owns the connection."""
        if self._epoch is None:
            raise NotPrimary("cannot start renewing a lease that was never claimed")
        if self._renewer is not None:
            return
        self._renewer = threading.Thread(
            target=self._renew_loop, name="wallet-primary-lease", daemon=True
        )
        self._renewer.start()

    def release(self) -> bool:
        """Hand the lease back so the next colour does not have to wait out the TTL."""
        self._stop.set()
        renewer, self._renewer = self._renewer, None
        if renewer is not None and renewer is not threading.current_thread():
            renewer.join(timeout=self._renew_interval + 5)

        with self._state_lock:
            epoch, self._epoch, self._deadline = self._epoch, None, 0.0
        if epoch is None:
            return False

        try:
            row = self._query("SELECT release_primary_cluster(%s, %s)", (self._color, epoch))
            released = bool(row and row[0])
        except Exception as exc:  # noqa: BLE001 - shutdown path must not raise
            # Not fatal: the lease expires on its own. It does mean the next
            # colour waits out the full TTL, so it is worth an alert.
            LOG.error("Could not release primary lease for %s (epoch %s): %s",
                      self._color, epoch, exc)
            return False

        LOG.info("Released primary lease for %s (epoch %s, released=%s)",
                 self._color, epoch, released)
        return released

    def close(self) -> None:
        self._stop.set()
        renewer, self._renewer = self._renewer, None
        if renewer is not None and renewer is not threading.current_thread():
            renewer.join(timeout=self._renew_interval + 5)
        self._close_conn()

    # ── renewer thread ───────────────────────────────────────────────────────

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._renew_interval):
            started = time.monotonic()
            try:
                row = self._query(
                    "SELECT renewed, lease_expires_at FROM renew_primary_lease(%s, %s, %s, %s)",
                    (self._color, self._node, self._epoch, self._ttl),
                )
            except Exception as exc:  # noqa: BLE001 - any failure is a renewal failure
                # A blip is survivable: the deadline, not this exception, decides
                # whether we are still primary. Drop the connection so the next
                # tick reconnects rather than reusing a broken socket.
                self._close_conn()
                with self._state_lock:
                    remaining = self._deadline - self._margin - time.monotonic()
                if remaining <= 0:
                    self._lose(f"could not renew lease before it expired: {exc}")
                    return
                LOG.warning(
                    "Primary lease renewal failed for %s, %.1fs of lease left: %s",
                    self._color, remaining, exc,
                )
                continue

            if not (row and row[0]):
                # The database says we are not the holder any more. This is the
                # normal path when the switchover released the lease under us.
                self._lose("renewal rejected: another colour owns the primary lease")
                return

            with self._state_lock:
                self._deadline = started + self._ttl

    def _lose(self, reason: str) -> None:
        """Demote to read-only, loudly, then hand off to the loss callback."""
        with self._state_lock:
            already_lost = self._epoch is None and self._lost_reason is not None
            self._epoch = None
            self._deadline = 0.0
            self._lost_reason = reason
        _mirror_read_only_env(True)
        if already_lost:
            return
        LOG.critical(
            "PRIMARY LEASE LOST on %s cluster: %s. Wallet writes are now refused.",
            self._color, reason,
        )
        self._close_conn()
        try:
            self._on_lost(reason)
        except Exception:  # noqa: BLE001 - callback must not mask the demotion
            LOG.exception("primary-lease loss callback raised")

    # ── the dedicated connection ─────────────────────────────────────────────

    def _query(self, sql: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._conn_lock:
            conn = self._conn
            if conn is None or conn.closed:
                conn = self._connect()
                self._conn = conn
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def _connect(self) -> Any:
        conn = psycopg2.connect(
            self._dsn,
            connect_timeout=5,
            # Surface a dead peer in ~10s instead of waiting for the kernel
            # default, so the lease lapses on time.
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=2,
            keepalives_count=3,
            application_name=f"wallet-lease-{self._color}",
            # A renewal that hangs is a renewal that failed.
            options="-c statement_timeout=3000",
        )
        conn.autocommit = True
        return conn

    def _close_conn(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - closing a broken socket is fine
                pass


# ── process-wide singleton ───────────────────────────────────────────────────

_LEASE: PrimaryLease | None = None


def claim_primary_or_standby() -> PrimaryLease:
    """Claim primary cluster status, or run as a read-only standby.

    With ``CLAIM_PRIMARY`` unset or false the pod stays a standby: the green
    cluster runs fully deployed and healthy for an hour before switchover without
    ever touching player balances. The switchover flips ``CLAIM_PRIMARY=true``,
    which rolls the deployment, and the new pods take the lease here.

    A pod that is told to claim and cannot is *not* a fatal error, and must not
    crash-loop. It is the expected state of the outgoing colour's pods if one of
    them restarts during the drain hour: another colour legitimately holds the
    lease. The pod comes up read-only, keeps serving reads while the old cluster
    drains, and refuses writes. Whether the handover itself succeeded is decided
    by switchover.sh querying ``current_primary_lease()``, which can abort and
    roll back; it is not inferred from a pod's exit code.
    """
    global _LEASE

    # Missing configuration is the one genuinely fatal case: KeyError here kills
    # the process before it can serve anything.
    dsn = os.environ["DATABASE_URL"]
    cluster_color = os.environ["CLUSTER_COLOR"]
    lease = PrimaryLease(dsn, cluster_color)
    _LEASE = lease

    if os.environ.get("CLAIM_PRIMARY", "false").strip().lower() not in _TRUTHY:
        LOG.info(
            "[STARTUP] CLAIM_PRIMARY not set; %s runs as read-only standby.", cluster_color
        )
        _mirror_read_only_env(True)
        return lease

    if not lease.try_claim():
        LOG.critical(
            "[STARTUP] %s was told to claim primary but another colour holds the lease. "
            "Running read-only; writes will be refused.", cluster_color,
        )
        _mirror_read_only_env(True)
        return lease

    lease.start()
    _mirror_read_only_env(False)
    _install_shutdown_hooks(lease)
    return lease


def primary_lease() -> PrimaryLease:
    if _LEASE is None:
        raise NotPrimary("claim_primary_or_standby() has not run")
    return _LEASE


def wallet_is_read_only() -> bool:
    """The authority for "may this process write?". Check it per request.

    Not ``os.environ['WALLET_READ_ONLY']``: the old code assigned to that
    variable at startup, which is invisible to any config already loaded and
    cannot express "was primary a second ago, is not now".
    """
    return _LEASE is None or not _LEASE.is_primary()


def fence_write(cur: Any) -> None:
    """Call as the first statement of every balance-mutating transaction."""
    primary_lease().fence(cur)


# ── helpers ──────────────────────────────────────────────────────────────────


def _mirror_read_only_env(read_only: bool) -> None:
    """Mirror the state into the environment for child processes and log scrapers.

    This is a mirror, not the source of truth. :func:`wallet_is_read_only` is.
    """
    os.environ["WALLET_READ_ONLY"] = "1" if read_only else "0"


def _default_on_lost(reason: str) -> None:
    """Default loss handler: stay up, stay read-only, make sure somebody knows.

    Deliberately not a process exit. Writes are already refused by the time this
    runs, and the outgoing colour has to keep serving reads for the five minutes
    that ``drain_old_cluster`` spends letting WebSockets close. Terminating here
    would turn every switchover into 5xx on the draining cluster.

    Set ``WALLET_EXIT_ON_LEASE_LOSS=1`` where a read-only wallet pod is worse than
    a missing one.
    """
    if os.environ.get("WALLET_EXIT_ON_LEASE_LOSS", "false").strip().lower() not in _TRUTHY:
        LOG.critical(
            "Wallet service is read-only after losing the primary lease (%s). "
            "Reads continue; writes are refused until it reclaims.", reason,
        )
        return

    LOG.critical("Terminating wallet service after losing primary lease: %s", reason)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        LOG.exception("could not signal self after losing primary lease")


def _install_shutdown_hooks(lease: PrimaryLease) -> None:
    """Release the lease on graceful shutdown so the next colour need not wait."""

    def _shutdown() -> None:
        lease.release()
        lease.close()

    atexit.register(_shutdown)

    if threading.current_thread() is not threading.main_thread():
        return

    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)

        def _handler(signum: int, frame: Any, _previous: Any = previous) -> None:
            _shutdown()
            if callable(_previous):
                _previous(signum, frame)
            else:
                raise SystemExit(128 + signum)

        try:
            signal.signal(sig, _handler)
        except (OSError, ValueError):
            LOG.warning("could not install %s handler for lease release", sig)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    _lease = claim_primary_or_standby()
    LOG.info(
        "wallet startup complete: colour=%s read_only=%s",
        _lease.cluster_color, wallet_is_read_only(),
    )
