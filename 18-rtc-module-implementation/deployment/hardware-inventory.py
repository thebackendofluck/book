#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
RTC Hardware Inventory & Health Monitoring Tool
================================================

Manages and monitors the fleet of RTC hardware modules deployed across
data centers in a regulated gambling platform.

GLI-11 Requirement: Section 5.4.2 requires operators to maintain an accurate
inventory of all time-critical hardware, including serial numbers, firmware
versions, calibration dates, and maintenance history. This tool automates
that requirement.

Features:
    - Discover RTC modules on I2C buses
    - Track hardware inventory (serial, firmware, calibration)
    - Monitor health metrics (temperature, battery, drift)
    - Generate compliance reports
    - Alert on degraded or failing modules
    - Export inventory for regulatory audits

Usage:
    python3 hardware-inventory.py discover --datacenter dc-east-1
    python3 hardware-inventory.py health --all
    python3 hardware-inventory.py report --format json --output inventory.json
    python3 hardware-inventory.py check --module rtc-dc-east-1-01
"""

import argparse
import json
import logging
import os
import sqlite3
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("rtc-inventory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("RTC_INVENTORY_DB", "/var/lib/rtc-service/inventory.db")
MAX_DRIFT_MS = 50  # GLI-11 compliant threshold
BATTERY_WARNING_PCT = 20.0
BATTERY_CRITICAL_PCT = 5.0
TEMP_WARNING_C = 60.0
TEMP_CRITICAL_C = 70.0
CALIBRATION_INTERVAL_DAYS = 365  # Annual recalibration required


class ModuleStatus(Enum):
    """Health status for RTC modules."""
    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"
    MAINTENANCE = "maintenance"
    QUARANTINED = "quarantined"
    DECOMMISSIONED = "decommissioned"


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class RTCModule:
    """Represents a physical RTC hardware module."""
    module_id: str
    datacenter: str
    serial_number: str
    manufacturer: str = "Maxim Integrated"
    model: str = "DS3231"
    firmware_version: str = "1.0"
    hardware_version: str = "1.0"

    # Physical location
    rack_id: str = ""
    slot_number: int = 0
    i2c_bus: int = 1
    i2c_address: int = 0x68

    # Status
    status: str = ModuleStatus.ACTIVE.value
    installed_date: str = ""
    last_calibration: str = ""
    next_calibration: str = ""
    last_maintenance: str = ""
    last_sync: str = ""

    # Current health metrics
    current_drift_ms: float = 0.0
    temperature_celsius: float = 25.0
    battery_voltage: float = 3.3
    battery_percentage: float = 100.0
    drift_rate_ppm: float = 0.0

    # Counters
    total_readings: int = 0
    failed_readings: int = 0
    consensus_participations: int = 0
    byzantine_fault_detections: int = 0

    # Metadata
    notes: str = ""
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class HealthCheck:
    """Result of a module health check."""
    module_id: str
    timestamp: str
    drift_ms: float
    temperature_c: float
    battery_pct: float
    status: str
    alerts: List[Dict[str, str]] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)


@dataclass
class Alert:
    """Alert for degraded or failing module."""
    module_id: str
    severity: str
    category: str
    message: str
    timestamp: str
    value: float = 0.0
    threshold: float = 0.0
    acknowledged: bool = False


# ---------------------------------------------------------------------------
# Database Layer
# ---------------------------------------------------------------------------
class InventoryDB:
    """SQLite database for RTC hardware inventory."""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS rtc_modules (
                module_id TEXT PRIMARY KEY,
                datacenter TEXT NOT NULL,
                serial_number TEXT UNIQUE NOT NULL,
                manufacturer TEXT DEFAULT 'Maxim Integrated',
                model TEXT DEFAULT 'DS3231',
                firmware_version TEXT DEFAULT '1.0',
                hardware_version TEXT DEFAULT '1.0',
                rack_id TEXT,
                slot_number INTEGER DEFAULT 0,
                i2c_bus INTEGER DEFAULT 1,
                i2c_address INTEGER DEFAULT 104,
                status TEXT DEFAULT 'active',
                installed_date TEXT,
                last_calibration TEXT,
                next_calibration TEXT,
                last_maintenance TEXT,
                last_sync TEXT,
                notes TEXT,
                tags TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                drift_ms REAL,
                temperature_c REAL,
                battery_pct REAL,
                status TEXT,
                alerts TEXT DEFAULT '[]',
                details TEXT DEFAULT '{}',
                FOREIGN KEY (module_id) REFERENCES rtc_modules(module_id)
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                value REAL DEFAULT 0,
                threshold REAL DEFAULT 0,
                acknowledged INTEGER DEFAULT 0,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                FOREIGN KEY (module_id) REFERENCES rtc_modules(module_id)
            );

            CREATE TABLE IF NOT EXISTS calibration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id TEXT NOT NULL,
                calibration_date TEXT NOT NULL,
                drift_before_ms REAL,
                drift_after_ms REAL,
                aging_offset_before INTEGER,
                aging_offset_after INTEGER,
                technician TEXT,
                notes TEXT,
                FOREIGN KEY (module_id) REFERENCES rtc_modules(module_id)
            );

            CREATE INDEX IF NOT EXISTS idx_health_module
                ON health_checks(module_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_module
                ON alerts(module_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_unacked
                ON alerts(acknowledged, severity);
        """)
        self.conn.commit()

    def upsert_module(self, module: RTCModule):
        """Insert or update an RTC module record."""
        self.conn.execute("""
            INSERT INTO rtc_modules (
                module_id, datacenter, serial_number, manufacturer, model,
                firmware_version, hardware_version, rack_id, slot_number,
                i2c_bus, i2c_address, status, installed_date, last_calibration,
                next_calibration, last_maintenance, last_sync, notes, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(module_id) DO UPDATE SET
                status=excluded.status,
                last_sync=excluded.last_sync,
                last_maintenance=excluded.last_maintenance,
                notes=excluded.notes,
                tags=excluded.tags,
                updated_at=datetime('now')
        """, (
            module.module_id, module.datacenter, module.serial_number,
            module.manufacturer, module.model, module.firmware_version,
            module.hardware_version, module.rack_id, module.slot_number,
            module.i2c_bus, module.i2c_address, module.status,
            module.installed_date, module.last_calibration,
            module.next_calibration, module.last_maintenance,
            module.last_sync, module.notes, json.dumps(module.tags),
        ))
        self.conn.commit()

    def get_module(self, module_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rtc_modules WHERE module_id = ?", (module_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_modules(self, datacenter: Optional[str] = None) -> List[dict]:
        if datacenter:
            rows = self.conn.execute(
                "SELECT * FROM rtc_modules WHERE datacenter = ? ORDER BY module_id",
                (datacenter,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM rtc_modules ORDER BY datacenter, module_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def record_health_check(self, check: HealthCheck):
        self.conn.execute("""
            INSERT INTO health_checks (module_id, timestamp, drift_ms,
                temperature_c, battery_pct, status, alerts, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            check.module_id, check.timestamp, check.drift_ms,
            check.temperature_c, check.battery_pct, check.status,
            json.dumps(check.alerts), json.dumps(check.details),
        ))
        self.conn.commit()

    def record_alert(self, alert: Alert):
        self.conn.execute("""
            INSERT INTO alerts (module_id, severity, category, message,
                timestamp, value, threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.module_id, alert.severity, alert.category,
            alert.message, alert.timestamp, alert.value, alert.threshold,
        ))
        self.conn.commit()

    def get_unacknowledged_alerts(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY severity, timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_health_history(self, module_id: str, hours: int = 24) -> List[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM health_checks WHERE module_id = ? AND timestamp > ? ORDER BY timestamp DESC",
            (module_id, cutoff)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Hardware Interface (simulated for portability)
# ---------------------------------------------------------------------------
class I2CInterface:
    """
    I2C interface for reading DS3231 RTC modules.

    In production, this reads from actual /dev/i2c-N devices.
    When hardware is unavailable (e.g., CI/CD), it uses simulated values.
    """

    def __init__(self):
        self.simulated = not os.path.exists("/dev/i2c-1")
        if self.simulated:
            logger.info("Hardware not detected; using simulated I2C interface")

    def read_time(self, bus: int, addr: int) -> Optional[datetime]:
        """Read current time from DS3231 RTC module."""
        if self.simulated:
            # Simulate with slight random drift
            import random
            drift_us = random.randint(-100, 100)
            return datetime.now(timezone.utc) + timedelta(microseconds=drift_us)

        try:
            import smbus2  # ty:ignore[unresolved-import]
            i2c = smbus2.SMBus(bus)
            data = i2c.read_i2c_block_data(addr, 0x00, 7)
            i2c.close()

            def bcd_to_dec(val): return (val // 16) * 10 + (val % 16)

            sec = bcd_to_dec(data[0] & 0x7F)
            minute = bcd_to_dec(data[1])
            hour = bcd_to_dec(data[2] & 0x3F)
            day = bcd_to_dec(data[4])
            month = bcd_to_dec(data[5] & 0x1F)
            year = 2000 + bcd_to_dec(data[6])

            return datetime(year, month, day, hour, minute, sec, tzinfo=timezone.utc)
        except Exception as e:
            logger.error(f"Failed to read time from bus={bus} addr=0x{addr:02x}: {e}")
            return None

    def read_temperature(self, bus: int, addr: int) -> Optional[float]:
        """Read temperature from DS3231 built-in sensor."""
        if self.simulated:
            import random
            return 25.0 + random.uniform(-2.0, 5.0)

        try:
            import smbus2  # ty:ignore[unresolved-import]
            i2c = smbus2.SMBus(bus)
            msb = i2c.read_byte_data(addr, 0x11)
            lsb = i2c.read_byte_data(addr, 0x12)
            i2c.close()

            # Convert to celsius (MSB is integer, top 2 bits of LSB are fraction)
            temp = msb + (lsb >> 6) * 0.25
            if msb & 0x80:  # Negative temperature
                temp = temp - 256
            return temp
        except Exception as e:
            logger.error(f"Failed to read temperature from bus={bus}: {e}")
            return None

    def read_aging_offset(self, bus: int, addr: int) -> Optional[int]:
        """Read aging offset register (drift compensation)."""
        if self.simulated:
            return 0

        try:
            import smbus2  # ty:ignore[unresolved-import]
            i2c = smbus2.SMBus(bus)
            offset = i2c.read_byte_data(addr, 0x10)
            i2c.close()
            # Convert to signed byte
            if offset > 127:
                offset -= 256
            return offset
        except Exception as e:
            logger.error(f"Failed to read aging offset: {e}")
            return None

    def scan_bus(self, bus: int) -> List[int]:
        """Scan I2C bus for devices."""
        if self.simulated:
            return [0x68]  # Simulate one DS3231

        devices = []
        try:
            import smbus2  # ty:ignore[unresolved-import]
            i2c = smbus2.SMBus(bus)
            for addr in range(0x03, 0x78):
                try:
                    i2c.read_byte(addr)
                    devices.append(addr)
                except OSError:
                    pass
            i2c.close()
        except Exception as e:
            logger.error(f"Failed to scan bus {bus}: {e}")
        return devices


# ---------------------------------------------------------------------------
# Health Check Engine
# ---------------------------------------------------------------------------
class HealthChecker:
    """Performs health checks on RTC modules."""

    def __init__(self, db: InventoryDB, i2c: I2CInterface):
        self.db = db
        self.i2c = i2c

    def check_module(self, module: dict) -> HealthCheck:
        """Run comprehensive health check on a single module."""
        now = datetime.now(timezone.utc)
        alerts = []
        details = {}

        # Read current time from hardware
        rtc_time = self.i2c.read_time(module["i2c_bus"], module["i2c_address"])
        if rtc_time is None:
            return HealthCheck(
                module_id=module["module_id"],
                timestamp=now.isoformat(),
                drift_ms=float("inf"),
                temperature_c=0.0,
                battery_pct=0.0,
                status=ModuleStatus.FAILED.value,
                alerts=[{
                    "severity": AlertSeverity.CRITICAL.value,
                    "message": "Unable to read time from RTC hardware"
                }],
            )

        # Calculate drift
        drift_ms = abs((rtc_time - now).total_seconds() * 1000)
        details["rtc_time"] = rtc_time.isoformat()
        details["system_time"] = now.isoformat()

        # Temperature
        temperature = self.i2c.read_temperature(
            module["i2c_bus"], module["i2c_address"]
        ) or 0.0

        # Battery (DS3231 doesn't report battery directly; estimate from voltage)
        # In production, use external ADC or Zymkey battery monitoring
        battery_pct = 100.0  # Placeholder for DS3231

        # Determine status and generate alerts
        status = ModuleStatus.ACTIVE.value

        # Drift check (GLI-11 Section 5.4)
        if drift_ms > MAX_DRIFT_MS:
            status = ModuleStatus.DEGRADED.value
            alert = Alert(
                module_id=module["module_id"],
                severity=AlertSeverity.CRITICAL.value,
                category="drift",
                message=f"Drift {drift_ms:.2f}ms exceeds GLI-11 threshold of {MAX_DRIFT_MS}ms",
                timestamp=now.isoformat(),
                value=drift_ms,
                threshold=MAX_DRIFT_MS,
            )
            alerts.append(asdict(alert))
            self.db.record_alert(alert)

        # Temperature check
        if temperature > TEMP_CRITICAL_C:
            status = ModuleStatus.DEGRADED.value
            alert = Alert(
                module_id=module["module_id"],
                severity=AlertSeverity.CRITICAL.value,
                category="temperature",
                message=f"Temperature {temperature:.1f}C exceeds critical threshold {TEMP_CRITICAL_C}C",
                timestamp=now.isoformat(),
                value=temperature,
                threshold=TEMP_CRITICAL_C,
            )
            alerts.append(asdict(alert))
            self.db.record_alert(alert)
        elif temperature > TEMP_WARNING_C:
            alert = Alert(
                module_id=module["module_id"],
                severity=AlertSeverity.WARNING.value,
                category="temperature",
                message=f"Temperature {temperature:.1f}C approaching warning threshold",
                timestamp=now.isoformat(),
                value=temperature,
                threshold=TEMP_WARNING_C,
            )
            alerts.append(asdict(alert))
            self.db.record_alert(alert)

        # Calibration check
        if module.get("next_calibration"):
            next_cal = datetime.fromisoformat(module["next_calibration"])
            if next_cal.tzinfo is None:
                next_cal = next_cal.replace(tzinfo=timezone.utc)
            days_until = (next_cal - now).days
            if days_until < 0:
                alert = Alert(
                    module_id=module["module_id"],
                    severity=AlertSeverity.WARNING.value,
                    category="calibration",
                    message=f"Calibration overdue by {abs(days_until)} days",
                    timestamp=now.isoformat(),
                    value=float(abs(days_until)),
                    threshold=0.0,
                )
                alerts.append(asdict(alert))
                self.db.record_alert(alert)
            elif days_until < 30:
                details["calibration_due_in_days"] = str(days_until)

        check = HealthCheck(
            module_id=module["module_id"],
            timestamp=now.isoformat(),
            drift_ms=drift_ms,
            temperature_c=temperature,
            battery_pct=battery_pct,
            status=status,
            alerts=alerts,
            details=details,
        )

        self.db.record_health_check(check)
        return check

    def check_all(self, datacenter: Optional[str] = None) -> List[HealthCheck]:
        """Run health checks on all modules."""
        modules = self.db.get_all_modules(datacenter)
        results = []
        for module in modules:
            if module["status"] == ModuleStatus.DECOMMISSIONED.value:
                continue
            result = self.check_module(module)
            results.append(result)
            logger.info(
                f"  {module['module_id']}: status={result.status} "
                f"drift={result.drift_ms:.2f}ms temp={result.temperature_c:.1f}C"
            )
        return results


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_modules(datacenter: str, db: InventoryDB, i2c: I2CInterface) -> List[RTCModule]:
    """Discover RTC modules on I2C buses."""
    logger.info(f"Discovering RTC modules for datacenter: {datacenter}")
    modules = []

    # Scan buses 0-10 for DS3231 devices
    for bus in range(11):
        if not os.path.exists(f"/dev/i2c-{bus}") and not i2c.simulated:
            continue

        devices = i2c.scan_bus(bus)
        for addr in devices:
            if addr == 0x68:  # DS3231 address
                module_num = len(modules) + 1
                module_id = f"rtc-{datacenter}-{module_num:02d}"

                # Read temperature to verify it's a DS3231
                temp = i2c.read_temperature(bus, addr)
                if temp is not None:
                    now = datetime.now(timezone.utc).isoformat()
                    next_cal = (datetime.now(timezone.utc) + timedelta(days=CALIBRATION_INTERVAL_DAYS)).isoformat()

                    module = RTCModule(
                        module_id=module_id,
                        datacenter=datacenter,
                        serial_number=f"DS3231-{datacenter}-{bus:02d}-{addr:02X}-{module_num:04d}",
                        i2c_bus=bus,
                        i2c_address=addr,
                        installed_date=now,
                        last_calibration=now,
                        next_calibration=next_cal,
                        temperature_celsius=temp,
                    )
                    modules.append(module)
                    db.upsert_module(module)
                    logger.info(f"  Discovered {module_id} on bus={bus} addr=0x{addr:02X} temp={temp:.1f}C")

    logger.info(f"Discovered {len(modules)} RTC module(s)")
    return modules


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def generate_report(db: InventoryDB, fmt: str = "json", datacenter: Optional[str] = None) -> str:
    """
    Generate compliance-ready inventory report.

    GLI-11 Section 5.4.3 requires operators to maintain documentation of
    all time-critical hardware including installation dates, calibration
    history, and current health status.
    """
    modules = db.get_all_modules(datacenter)
    unacked_alerts = db.get_unacknowledged_alerts()

    report: Dict[str, Any] = {
        "report_type": "RTC Hardware Inventory & Health Report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_standard": "GLI-11 Section 5.4",
        "datacenter_filter": datacenter or "ALL",
        "summary": {
            "total_modules": len(modules),
            "active": sum(1 for m in modules if m["status"] == "active"),
            "degraded": sum(1 for m in modules if m["status"] == "degraded"),
            "failed": sum(1 for m in modules if m["status"] == "failed"),
            "maintenance": sum(1 for m in modules if m["status"] == "maintenance"),
            "unacknowledged_alerts": len(unacked_alerts),
        },
        "modules": modules,
        "unacknowledged_alerts": unacked_alerts,
    }

    if fmt == "json":
        return json.dumps(report, indent=2, default=str)
    elif fmt == "text":
        lines = [
            "=" * 72,
            "RTC HARDWARE INVENTORY REPORT",
            f"Generated: {report['generated_at']}",
            f"Standard:  GLI-11 Section 5.4",
            "=" * 72,
            "",
            f"Total Modules: {report['summary']['total_modules']}",
            f"  Active:      {report['summary']['active']}",
            f"  Degraded:    {report['summary']['degraded']}",
            f"  Failed:      {report['summary']['failed']}",
            f"  Maintenance: {report['summary']['maintenance']}",
            f"  Alerts:      {report['summary']['unacknowledged_alerts']}",
            "",
            "-" * 72,
            f"{'Module ID':<28} {'DC':<14} {'Status':<12} {'Serial':<20}",
            "-" * 72,
        ]
        for m in modules:
            lines.append(
                f"{m['module_id']:<28} {m['datacenter']:<14} "
                f"{m['status']:<12} {m['serial_number']:<20}"
            )
        lines.append("=" * 72)

        if unacked_alerts:
            lines.append("")
            lines.append("UNACKNOWLEDGED ALERTS:")
            lines.append("-" * 72)
            for a in unacked_alerts:
                lines.append(f"  [{a['severity']}] {a['module_id']}: {a['message']}")

        return "\n".join(lines)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="RTC Hardware Inventory & Health Monitoring (GLI-11 Compliant)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # discover
    disc_parser = subparsers.add_parser("discover", help="Discover RTC modules on I2C buses")
    disc_parser.add_argument("--datacenter", required=True, help="Data center identifier")

    # health
    health_parser = subparsers.add_parser("health", help="Run health checks")
    health_parser.add_argument("--all", action="store_true", help="Check all modules")
    health_parser.add_argument("--datacenter", help="Filter by data center")
    health_parser.add_argument("--module", help="Check specific module")

    # report
    report_parser = subparsers.add_parser("report", help="Generate inventory report")
    report_parser.add_argument("--format", choices=["json", "text"], default="json")
    report_parser.add_argument("--output", help="Output file (default: stdout)")
    report_parser.add_argument("--datacenter", help="Filter by data center")

    # check
    check_parser = subparsers.add_parser("check", help="Check single module status")
    check_parser.add_argument("--module", required=True, help="Module ID")

    # alerts
    alerts_parser = subparsers.add_parser("alerts", help="Show unacknowledged alerts")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    db = InventoryDB()
    i2c = I2CInterface()

    try:
        if args.command == "discover":
            modules = discover_modules(args.datacenter, db, i2c)
            print(f"\nDiscovered {len(modules)} module(s)")
            for m in modules:
                print(f"  {m.module_id}: bus={m.i2c_bus} addr=0x{m.i2c_address:02X}")

        elif args.command == "health":
            checker = HealthChecker(db, i2c)
            if args.module:
                module = db.get_module(args.module)
                if module is None:
                    print(f"Module not found: {args.module}")
                    sys.exit(1)
                assert module is not None
                result = checker.check_module(module)
                print(json.dumps(asdict(result), indent=2))
            else:
                results = checker.check_all(args.datacenter)
                print(f"\nHealth check complete: {len(results)} module(s) checked")
                for r in results:
                    symbol = "OK" if r.status == "active" else "!!"
                    print(f"  [{symbol}] {r.module_id}: {r.status} drift={r.drift_ms:.2f}ms")

        elif args.command == "report":
            output = generate_report(db, args.format, args.datacenter)
            if args.output:
                Path(args.output).write_text(output)
                print(f"Report written to {args.output}")
            else:
                print(output)

        elif args.command == "check":
            module = db.get_module(args.module)
            if module is None:
                print(f"Module not found: {args.module}")
                sys.exit(1)
            assert module is not None
            checker = HealthChecker(db, i2c)
            result = checker.check_module(module)
            print(json.dumps(asdict(result), indent=2))

        elif args.command == "alerts":
            alerts = db.get_unacknowledged_alerts()
            if not alerts:
                print("No unacknowledged alerts")
            else:
                for a in alerts:
                    print(f"  [{a['severity']}] {a['module_id']}: {a['message']} ({a['timestamp']})")

    finally:
        db.close()


if __name__ == "__main__":
    main()
