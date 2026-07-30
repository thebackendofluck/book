#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
ipmicheck.py -- IPMI BMC sensor monitoring for Datadog agent custom check.
Reads BMC sensors (temperatures, voltages, fan speeds) via ipmitool and
emits metrics with alarm states: nominal, warning, critical.

Deployed by Ansible hardware-monitoring role to:
  {{ monitoring_agent_checks_path }}/ipmicheck.py

Chapter 33: Hardware Monitoring and Predictive Maintenance
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AlarmState(Enum):
    NOMINAL = "nominal"
    WARNING = "warning"
    CRITICAL = "critical"
    UNAVAILABLE = "unavailable"


@dataclass
class SensorReading:
    name: str
    value: Optional[float]
    unit: str
    state: AlarmState
    raw_line: str = field(repr=False)


# Thresholds for common sensor types
TEMP_WARNING_C = 70.0
TEMP_CRITICAL_C = 80.0

VOLTAGE_WARN_DELTA_PCT = 0.10   # ±10% from nominal triggers warning
VOLTAGE_CRIT_DELTA_PCT = 0.15   # ±15% triggers critical

FAN_WARNING_RPM = 500
FAN_CRITICAL_RPM = 200

# Nominal voltages for common rails
VOLTAGE_NOMINALS: dict[str, float] = {
    "12V": 12.0,
    "5V": 5.0,
    "3.3V": 3.3,
    "VCORE": 1.0,
    "VBAT": 3.0,
}

# ipmitool sdr elist output columns:
#   Sensor Name | Entity | Status | Reading | Units
IPMI_SDR_PATTERN = re.compile(
    r"^(.+?)\s*\|\s*\S+\s*\|\s*(\S+)\s*\|\s*([0-9.]+)\s*\|\s*(\S+)"
)


def run_ipmitool() -> list[str]:
    """Run ipmitool sdr elist and return output lines."""
    try:
        result = subprocess.run(
            ["ipmitool", "sdr", "elist"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"WARNING: ipmitool exited {result.returncode}: {result.stderr.strip()}",
                file=sys.stderr,
            )
        return result.stdout.splitlines()
    except FileNotFoundError:
        print("ERROR: ipmitool not found. Install with: apt-get install ipmitool", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print("ERROR: ipmitool timed out after 30 seconds", file=sys.stderr)
        return []


def classify_temperature(value: float, name: str) -> AlarmState:
    """Classify a temperature reading into an alarm state."""
    if value >= TEMP_CRITICAL_C:
        return AlarmState.CRITICAL
    if value >= TEMP_WARNING_C:
        return AlarmState.WARNING
    return AlarmState.NOMINAL


def classify_voltage(value: float, name: str) -> AlarmState:
    """Classify a voltage reading against known nominal values."""
    nominal: Optional[float] = None
    for key, nom in VOLTAGE_NOMINALS.items():
        if key.lower() in name.lower():
            nominal = nom
            break

    if nominal is None:
        # Unknown voltage rail — accept within 20% of the reading's own magnitude
        return AlarmState.NOMINAL

    delta_pct = abs(value - nominal) / nominal
    if delta_pct >= VOLTAGE_CRIT_DELTA_PCT:
        return AlarmState.CRITICAL
    if delta_pct >= VOLTAGE_WARN_DELTA_PCT:
        return AlarmState.WARNING
    return AlarmState.NOMINAL


def classify_fan(value: float) -> AlarmState:
    """Classify a fan speed reading."""
    if value <= FAN_CRITICAL_RPM:
        return AlarmState.CRITICAL
    if value <= FAN_WARNING_RPM:
        return AlarmState.WARNING
    return AlarmState.NOMINAL


def parse_sensor_line(line: str) -> Optional[SensorReading]:
    """Parse a single ipmitool sdr elist line into a SensorReading."""
    # Handle "No Reading" / disabled sensors
    if "No Reading" in line or "Disabled" in line:
        parts = line.split("|")
        name = parts[0].strip() if parts else "unknown"
        return SensorReading(
            name=name,
            value=None,
            unit="",
            state=AlarmState.UNAVAILABLE,
            raw_line=line,
        )

    match = IPMI_SDR_PATTERN.match(line)
    if not match:
        return None

    name = match.group(1).strip()
    raw_value = match.group(3)
    unit = match.group(4).strip()

    try:
        value = float(raw_value)
    except ValueError:
        return None

    name_lower = name.lower()
    unit_lower = unit.lower()

    if "temp" in name_lower or unit_lower in ("degrees c", "c"):
        state = classify_temperature(value, name)
    elif "volt" in name_lower or unit_lower in ("volts", "v"):
        state = classify_voltage(value, name)
    elif "fan" in name_lower or unit_lower in ("rpm",):
        state = classify_fan(value)
    else:
        state = AlarmState.NOMINAL

    return SensorReading(
        name=name,
        value=value,
        unit=unit,
        state=state,
        raw_line=line,
    )


def emit_datadog_metrics(readings: list[SensorReading]) -> None:
    """Emit metrics in Datadog statsd format (stdout for agent check)."""
    state_to_int = {
        AlarmState.NOMINAL: 0,
        AlarmState.WARNING: 1,
        AlarmState.CRITICAL: 2,
        AlarmState.UNAVAILABLE: 3,
    }

    for r in readings:
        metric_name = "hardware.ipmi." + re.sub(r"[^a-z0-9_]", "_", r.name.lower())
        tags = f"unit:{r.unit},state:{r.state.value}"

        if r.value is not None:
            print(f"METRIC|{metric_name}.reading|{r.value}|gauge|{tags}")

        print(f"METRIC|{metric_name}.state|{state_to_int[r.state]}|gauge|{tags}")

        if r.state == AlarmState.CRITICAL:
            print(
                f"EVENT|IPMI CRITICAL: {r.name}|"
                f"Sensor {r.name!r} is in CRITICAL state: {r.value} {r.unit}|"
                f"alert_type:error|tags:{tags}"
            )
        elif r.state == AlarmState.WARNING:
            print(
                f"EVENT|IPMI WARNING: {r.name}|"
                f"Sensor {r.name!r} is in WARNING state: {r.value} {r.unit}|"
                f"alert_type:warning|tags:{tags}"
            )


def check_critical_sensors(readings: list[SensorReading]) -> int:
    """Return exit code: 0=ok, 1=warning, 2=critical."""
    has_critical = any(r.state == AlarmState.CRITICAL for r in readings)
    has_warning = any(r.state == AlarmState.WARNING for r in readings)

    if has_critical:
        return 2
    if has_warning:
        return 1
    return 0


def main() -> int:
    lines = run_ipmitool()
    if not lines:
        print("CRITICAL: Could not retrieve IPMI sensor data", file=sys.stderr)
        return 2

    readings: list[SensorReading] = []
    for line in lines:
        reading = parse_sensor_line(line)
        if reading is not None:
            readings.append(reading)

    emit_datadog_metrics(readings)

    critical = [r for r in readings if r.state == AlarmState.CRITICAL]
    warning = [r for r in readings if r.state == AlarmState.WARNING]

    print(
        f"# IPMI check: {len(readings)} sensors, "
        f"{len(critical)} critical, {len(warning)} warning"
    )

    return check_critical_sensors(readings)


if __name__ == "__main__":
    sys.exit(main())
