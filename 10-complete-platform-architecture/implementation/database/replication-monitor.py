#!/usr/bin/env python3
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
Chapter 42 - Complete Platform Architecture
Database Replication Lag Monitor for iGambling Hub-and-Spoke Pattern

Monitors replication lag across hub-to-spoke and spoke-to-replica
connections. Alerts when lag exceeds thresholds critical for gambling
platform operations (e.g., wallet balance consistency).

Usage:
    python replication-monitor.py --config config.json
    python replication-monitor.py --check-once
    python replication-monitor.py --daemon --interval 30

Dependencies:
    pip install psycopg2-binary prometheus_client requests
"""

import argparse
import json
from typing import Any
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("Install psycopg2: pip install psycopg2-binary")
    sys.exit(1)

try:
    from prometheus_client import Gauge, start_http_server, Counter
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("replication-monitor")


# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "databases": {
        "hub": {
            "host": "hub-db",
            "port": 5432,
            "dbname": "casino_hub",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "hub"
        },
        "payment_spoke": {
            "host": "payment-db",
            "port": 5432,
            "dbname": "casino_payments",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "spoke"
        },
        "game_spoke": {
            "host": "game-db",
            "port": 5432,
            "dbname": "casino_games",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "spoke"
        },
        "player_spoke": {
            "host": "player-db",
            "port": 5432,
            "dbname": "casino_players",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "spoke"
        },
        "compliance_spoke": {
            "host": "compliance-db",
            "port": 5432,
            "dbname": "casino_compliance",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "spoke"
        },
        "analytics_replica": {
            "host": "analytics-db",
            "port": 5432,
            "dbname": "casino_analytics",
            "user": "monitor_user",
            "password": "CHANGE_ME",
            "role": "replica"
        }
    },
    "thresholds": {
        "warning_lag_bytes": 1048576,       # 1MB
        "critical_lag_bytes": 10485760,     # 10MB
        "warning_lag_seconds": 5,
        "critical_lag_seconds": 30,
        "payment_critical_seconds": 2,      # Payment requires tighter SLO
        "max_slot_retained_wal_gb": 5
    },
    "alerting": {
        "slack_webhook_url": "",
        "pagerduty_routing_key": "",
        "alert_cooldown_minutes": 5
    },
    "prometheus": {
        "enabled": True,
        "port": 9187
    },
    "check_interval_seconds": 30
}


@dataclass
class ReplicationStatus:
    source: str
    target: str
    slot_name: str
    status: str                     # 'streaming', 'catchup', 'stopped'
    lag_bytes: int
    lag_seconds: Optional[float]
    sent_lsn: str
    write_lsn: str
    flush_lsn: str
    replay_lsn: str
    sync_state: str
    checked_at: datetime

    @property
    def is_healthy(self) -> bool:
        return self.status == "streaming" and self.lag_bytes < 10485760

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "slot_name": self.slot_name,
            "status": self.status,
            "lag_bytes": self.lag_bytes,
            "lag_seconds": self.lag_seconds,
            "sent_lsn": self.sent_lsn,
            "write_lsn": self.write_lsn,
            "flush_lsn": self.flush_lsn,
            "replay_lsn": self.replay_lsn,
            "sync_state": self.sync_state,
            "checked_at": self.checked_at.isoformat()
        }


# ──────────────────────────────────────────────────────────────
# Prometheus metrics
# ──────────────────────────────────────────────────────────────

if PROMETHEUS_AVAILABLE:
    REPLICATION_LAG_BYTES = Gauge(
        "casino_replication_lag_bytes",
        "Replication lag in bytes",
        ["source", "target", "slot_name"]
    )
    REPLICATION_LAG_SECONDS = Gauge(
        "casino_replication_lag_seconds",
        "Replication lag in seconds",
        ["source", "target", "slot_name"]
    )
    REPLICATION_STATUS = Gauge(
        "casino_replication_status",
        "Replication status (1=streaming, 0=other)",
        ["source", "target", "slot_name"]
    )
    REPLICATION_CHECK_ERRORS = Counter(
        "casino_replication_check_errors_total",
        "Total replication check errors",
        ["database"]
    )
    SLOT_RETAINED_WAL_BYTES = Gauge(
        "casino_slot_retained_wal_bytes",
        "WAL retained by replication slot",
        ["database", "slot_name"]
    )


class ReplicationMonitor:
    def __init__(self, config: dict):
        self.config = config
        self.thresholds = config["thresholds"]
        self.alert_history = {}
        self.running = True

    def _connect(self, db_config: dict):
        """Create database connection."""
        return psycopg2.connect(
            host=db_config["host"],
            port=db_config["port"],
            dbname=db_config["dbname"],
            user=db_config["user"],
            password=db_config["password"],
            connect_timeout=10
        )

    def check_publisher_replication(self, db_name: str, db_config: dict) -> list:
        """Check replication status from publisher (hub or spoke) side."""
        results = []
        try:
            conn = self._connect(db_config)
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Check active replication connections
            cur.execute("""
                SELECT
                    client_addr,
                    application_name,
                    state,
                    sent_lsn::text,
                    write_lsn::text,
                    flush_lsn::text,
                    replay_lsn::text,
                    sync_state,
                    pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes,
                    EXTRACT(EPOCH FROM (now() - reply_time)) AS lag_seconds,
                    slot_name
                FROM pg_stat_replication
                ORDER BY application_name
            """)
            for row in cur.fetchall():
                status = ReplicationStatus(
                    source=db_name,
                    target=row["application_name"] or str(row["client_addr"]),
                    slot_name=row["slot_name"] or "none",
                    status=row["state"] or "unknown",
                    lag_bytes=int(row["lag_bytes"] or 0),
                    lag_seconds=float(row["lag_seconds"]) if row["lag_seconds"] else None,
                    sent_lsn=row["sent_lsn"] or "",
                    write_lsn=row["write_lsn"] or "",
                    flush_lsn=row["flush_lsn"] or "",
                    replay_lsn=row["replay_lsn"] or "",
                    sync_state=row["sync_state"] or "",
                    checked_at=datetime.now(timezone.utc)
                )
                results.append(status)

                # Update Prometheus metrics
                if PROMETHEUS_AVAILABLE:
                    labels = [db_name, status.target, status.slot_name]
                    REPLICATION_LAG_BYTES.labels(*labels).set(status.lag_bytes)
                    if status.lag_seconds is not None:
                        REPLICATION_LAG_SECONDS.labels(*labels).set(status.lag_seconds)
                    REPLICATION_STATUS.labels(*labels).set(
                        1 if status.status == "streaming" else 0
                    )

            # Check replication slots (detect orphaned slots consuming WAL)
            cur.execute("""
                SELECT
                    slot_name,
                    slot_type,
                    active,
                    pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes
                FROM pg_replication_slots
            """)
            for row in cur.fetchall():
                retained_bytes = int(row["retained_bytes"] or 0)
                if PROMETHEUS_AVAILABLE:
                    SLOT_RETAINED_WAL_BYTES.labels(db_name, row["slot_name"]).set(retained_bytes)

                retained_gb = retained_bytes / (1024 ** 3)
                if retained_gb > self.thresholds["max_slot_retained_wal_gb"]:
                    logger.warning(
                        f"Slot {row['slot_name']} on {db_name} retaining "
                        f"{retained_gb:.2f} GB WAL (active={row['active']})"
                    )
                    if not row["active"]:
                        self._alert(
                            f"CRITICAL: Inactive replication slot '{row['slot_name']}' "
                            f"on {db_name} retaining {retained_gb:.2f} GB WAL. "
                            f"Consider dropping: SELECT pg_drop_replication_slot('{row['slot_name']}');",
                            severity="critical"
                        )

            cur.close()
            conn.close()

        except psycopg2.Error as e:
            logger.error(f"Failed to check replication on {db_name}: {e}")
            if PROMETHEUS_AVAILABLE:
                REPLICATION_CHECK_ERRORS.labels(db_name).inc()

        return results

    def check_subscriber_replication(self, db_name: str, db_config: dict) -> list:
        """Check subscription status from subscriber (spoke or replica) side."""
        results = []
        try:
            conn = self._connect(db_config)
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cur.execute("""
                SELECT
                    subname,
                    subenabled,
                    subconninfo,
                    pid,
                    received_lsn::text,
                    latest_end_lsn::text,
                    EXTRACT(EPOCH FROM (now() - latest_end_time)) AS seconds_behind
                FROM pg_subscription s
                LEFT JOIN pg_stat_subscription ss ON s.oid = ss.subid
            """)
            for row in cur.fetchall():
                lag_seconds = float(row["seconds_behind"]) if row["seconds_behind"] else None

                status = ReplicationStatus(
                    source="hub",
                    target=db_name,
                    slot_name=row["subname"],
                    status="streaming" if row["pid"] else "stopped",
                    lag_bytes=0,
                    lag_seconds=lag_seconds,
                    sent_lsn="",
                    write_lsn="",
                    flush_lsn="",
                    replay_lsn=row["received_lsn"] or "",
                    sync_state="enabled" if row["subenabled"] else "disabled",
                    checked_at=datetime.now(timezone.utc)
                )
                results.append(status)

                if not row["pid"]:
                    self._alert(
                        f"CRITICAL: Subscription '{row['subname']}' on {db_name} "
                        f"has no active worker (pid=None). Data is stale.",
                        severity="critical"
                    )

            cur.close()
            conn.close()

        except psycopg2.Error as e:
            logger.error(f"Failed to check subscriptions on {db_name}: {e}")
            if PROMETHEUS_AVAILABLE:
                REPLICATION_CHECK_ERRORS.labels(db_name).inc()

        return results

    def evaluate_thresholds(self, statuses: list):
        """Evaluate replication statuses against configured thresholds."""
        for status in statuses:
            db_key = f"{status.source}->{status.target}"

            # Check if replication has stopped
            if status.status != "streaming":
                self._alert(
                    f"CRITICAL: Replication {db_key} (slot={status.slot_name}) "
                    f"is in state '{status.status}', not streaming!",
                    severity="critical"
                )
                continue

            # Check byte lag
            if status.lag_bytes > self.thresholds["critical_lag_bytes"]:
                lag_mb = status.lag_bytes / (1024 * 1024)
                self._alert(
                    f"CRITICAL: Replication lag {db_key} is {lag_mb:.2f} MB "
                    f"(threshold: {self.thresholds['critical_lag_bytes'] / (1024*1024):.0f} MB)",
                    severity="critical"
                )
            elif status.lag_bytes > self.thresholds["warning_lag_bytes"]:
                lag_mb = status.lag_bytes / (1024 * 1024)
                self._alert(
                    f"WARNING: Replication lag {db_key} is {lag_mb:.2f} MB",
                    severity="warning"
                )

            # Check time lag
            if status.lag_seconds is not None:
                # Payment databases have tighter SLO
                is_payment = "payment" in status.source.lower() or "payment" in status.target.lower()
                critical_threshold = (
                    self.thresholds["payment_critical_seconds"]
                    if is_payment
                    else self.thresholds["critical_lag_seconds"]
                )

                if status.lag_seconds > critical_threshold:
                    self._alert(
                        f"CRITICAL: Replication time lag {db_key} is "
                        f"{status.lag_seconds:.1f}s (threshold: {critical_threshold}s)"
                        + (" [PAYMENT - TIGHT SLO]" if is_payment else ""),
                        severity="critical"
                    )
                elif status.lag_seconds > self.thresholds["warning_lag_seconds"]:
                    self._alert(
                        f"WARNING: Replication time lag {db_key} is "
                        f"{status.lag_seconds:.1f}s",
                        severity="warning"
                    )

    def _alert(self, message: str, severity: str = "warning"):
        """Send alert with cooldown to prevent spam."""
        alert_key = message[:100]
        cooldown = timedelta(minutes=self.config["alerting"]["alert_cooldown_minutes"])

        if alert_key in self.alert_history:
            if datetime.now(timezone.utc) - self.alert_history[alert_key] < cooldown:
                return
        self.alert_history[alert_key] = datetime.now(timezone.utc)

        if severity == "critical":
            logger.critical(message)
        else:
            logger.warning(message)

        # Send to Slack
        webhook = self.config["alerting"].get("slack_webhook_url")
        if webhook and REQUESTS_AVAILABLE:
            try:
                color = "#FF0000" if severity == "critical" else "#FFA500"
                requests.post(webhook, json={
                    "attachments": [{
                        "color": color,
                        "title": f"Casino Replication {severity.upper()}",
                        "text": message,
                        "ts": int(time.time())
                    }]
                }, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send Slack alert: {e}")

    def run_check(self) -> dict:
        """Run a single replication check across all databases."""
        all_statuses = []
        summary: dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "total_slots": 0,
            "healthy": 0,
            "warning": 0,
            "critical": 0,
            "databases": {}
        }

        for db_name, db_config in self.config["databases"].items():
            role = db_config.get("role", "spoke")

            if role in ("hub", "spoke"):
                statuses = self.check_publisher_replication(db_name, db_config)
                all_statuses.extend(statuses)

            if role in ("spoke", "replica"):
                sub_statuses = self.check_subscriber_replication(db_name, db_config)
                all_statuses.extend(sub_statuses)

            db_statuses = [s for s in all_statuses if s.source == db_name or s.target == db_name]
            summary["databases"][db_name] = {
                "role": role,
                "slots": len(db_statuses),
                "statuses": [s.to_dict() for s in db_statuses]
            }

        self.evaluate_thresholds(all_statuses)

        summary["total_slots"] = len(all_statuses)
        summary["healthy"] = sum(1 for s in all_statuses if s.is_healthy)
        summary["critical"] = sum(1 for s in all_statuses if not s.is_healthy)

        return summary

    def run_daemon(self, interval: int):
        """Run continuous monitoring loop."""
        logger.info(f"Starting replication monitor daemon (interval={interval}s)")

        if PROMETHEUS_AVAILABLE and self.config["prometheus"]["enabled"]:
            port = self.config["prometheus"]["port"]
            start_http_server(port)
            logger.info(f"Prometheus metrics available on :{port}/metrics")

        def handle_signal(signum, frame):
            logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        while self.running:
            try:
                summary = self.run_check()
                logger.info(
                    f"Check complete: {summary['healthy']}/{summary['total_slots']} healthy"
                )
            except Exception as e:
                logger.error(f"Check cycle failed: {e}")

            # Sleep in small increments to allow signal handling
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("Monitor stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Casino Platform Replication Lag Monitor"
    )
    parser.add_argument("--config", help="Path to config JSON file")
    parser.add_argument("--check-once", action="store_true",
                        help="Run single check and exit")
    parser.add_argument("--daemon", action="store_true",
                        help="Run as continuous monitor")
    parser.add_argument("--interval", type=int, default=30,
                        help="Check interval in seconds (default: 30)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    if args.config:
        with open(args.config) as f:
            user_config = json.load(f)
            config.update(user_config)

    monitor = ReplicationMonitor(config)

    if args.daemon:
        monitor.run_daemon(args.interval)
    else:
        summary = monitor.run_check()
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"\nReplication Status Check - {summary['checked_at']}")
            print("=" * 60)
            print(f"Total slots: {summary['total_slots']}")
            print(f"Healthy:     {summary['healthy']}")
            print(f"Critical:    {summary['critical']}")
            print()
            for db_name, db_info in summary["databases"].items():
                print(f"  {db_name} ({db_info['role']}): {db_info['slots']} slots")
                for s in db_info["statuses"]:
                    lag_mb = s["lag_bytes"] / (1024 * 1024)
                    lag_s = f"{s['lag_seconds']:.1f}s" if s["lag_seconds"] else "N/A"
                    print(f"    {s['source']} -> {s['target']}: "
                          f"{s['status']} | lag={lag_mb:.2f}MB / {lag_s}")


if __name__ == "__main__":
    main()
