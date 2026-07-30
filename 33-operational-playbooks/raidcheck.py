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
raidcheck.py -- RAID controller health monitoring for Datadog agent custom check.
Monitors controller health, disk states, rebuild progress, and battery status
via MegaCLI/storcli (LSI/Broadcom) and mdadm (software RAID).

Deployed by Ansible hardware-monitoring role to:
  {{ monitoring_agent_checks_path }}/raidcheck.py

Chapter 33: Hardware Monitoring and Predictive Maintenance
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DriveState(Enum):
    ONLINE = "online"
    HOTSPARE = "hotspare"
    REBUILDING = "rebuilding"
    FAILED = "failed"
    UNCONFIGURED_GOOD = "unconfigured_good"
    UNCONFIGURED_BAD = "unconfigured_bad"
    UNKNOWN = "unknown"


class ControllerState(Enum):
    OPTIMAL = "optimal"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class DiskStatus:
    slot: int
    enclosure: int
    state: DriveState
    rebuild_pct: Optional[float]   # 0-100 when rebuilding, else None
    media_errors: int
    other_errors: int
    predictive_failure: bool


@dataclass
class RAIDArray:
    name: str           # e.g., "md0" or "VD0"
    level: str          # e.g., "RAID5", "RAID10"
    state: ControllerState
    size_gb: float
    free_gb: float
    disks: list[DiskStatus] = field(default_factory=list)
    battery_state: Optional[str] = None   # "Optimal", "Charging", "Failed"
    rebuild_pct: Optional[float] = None


# ---------------------------------------------------------------------------
# storcli / MegaCLI backend
# ---------------------------------------------------------------------------

STORCLI_PATH = "/opt/MegaRAID/storcli/storcli64"
MEGACLI_PATH = "/opt/MegaRAID/MegaCli/MegaCli64"


def _run(cmd: list[str], timeout: int = 30) -> Optional[str]:
    """Run a subprocess command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"WARNING: {cmd[0]} exited {result.returncode}: {result.stderr.strip()[:200]}",
                file=sys.stderr,
            )
        return result.stdout
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        print(f"ERROR: {cmd[0]} timed out after {timeout}s", file=sys.stderr)
        return None


def _map_drive_state(raw: str) -> DriveState:
    r = raw.strip().lower()
    if r in ("onln", "online"):
        return DriveState.ONLINE
    if r in ("dsbl", "dhs", "ghs", "hotspare"):
        return DriveState.HOTSPARE
    if r in ("rbld", "rebuild", "rebuilding"):
        return DriveState.REBUILDING
    if r in ("dfld", "failed", "fail"):
        return DriveState.FAILED
    if r in ("ugood", "unconfigured(good)"):
        return DriveState.UNCONFIGURED_GOOD
    if r in ("ubad", "unconfigured(bad)"):
        return DriveState.UNCONFIGURED_BAD
    return DriveState.UNKNOWN


def _parse_storcli_json(output: str) -> list[RAIDArray]:
    """Parse storcli JSON output into RAIDArray objects."""
    arrays: list[RAIDArray] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Failed to parse storcli JSON: {exc}", file=sys.stderr)
        return arrays

    controllers = data.get("Controllers", [])
    for ctrl in controllers:
        resp = ctrl.get("Response Data", {})
        vds = resp.get("VD LIST", [])
        pds = resp.get("PD LIST", [])

        # Map drives to virtual disks by their position
        disk_map: dict[str, DiskStatus] = {}
        for pd in pds:
            eid_slot = pd.get("EID:Slt", "0:0").split(":")
            enclosure = int(eid_slot[0]) if len(eid_slot) > 0 else 0
            slot = int(eid_slot[1]) if len(eid_slot) > 1 else 0

            state = _map_drive_state(pd.get("State", "unknown"))
            rebuild_pct: Optional[float] = None
            if state == DriveState.REBUILDING:
                rb = pd.get("Rbld", "0%").rstrip("%")
                try:
                    rebuild_pct = float(rb)
                except ValueError:
                    rebuild_pct = 0.0

            disk = DiskStatus(
                slot=slot,
                enclosure=enclosure,
                state=state,
                rebuild_pct=rebuild_pct,
                media_errors=int(pd.get("Med", 0)),
                other_errors=int(pd.get("Err", 0)),
                predictive_failure=pd.get("Pred Fail", "No").lower() == "yes",
            )
            disk_map[f"{enclosure}:{slot}"] = disk

        for vd in vds:
            vd_state_raw = vd.get("State", "unknown").lower()
            if vd_state_raw in ("optl", "optimal"):
                ctrl_state = ControllerState.OPTIMAL
            elif vd_state_raw in ("dgrd", "degraded"):
                ctrl_state = ControllerState.DEGRADED
            else:
                ctrl_state = ControllerState.FAILED

            size_str = vd.get("Size", "0 GB").replace("GB", "").strip()
            try:
                size_gb = float(size_str)
            except ValueError:
                size_gb = 0.0

            array = RAIDArray(
                name=f"VD{vd.get('DG/VD', '0/0')}",
                level=vd.get("TYPE", "unknown"),
                state=ctrl_state,
                size_gb=size_gb,
                free_gb=0.0,
                disks=list(disk_map.values()),
            )

            # Battery / BBU status
            bbu = resp.get("BBU_Info", [{}])
            if bbu:
                array.battery_state = bbu[0].get("State", "unknown")

            arrays.append(array)

    return arrays


def check_storcli() -> list[RAIDArray]:
    """Query storcli for RAID status."""
    output = _run([STORCLI_PATH, "/call", "show", "all", "J"])
    if output is None:
        output = _run([MEGACLI_PATH, "-CfgDsply", "-aALL", "-NoLog"])
        if output is None:
            return []
        # MegaCLI text parsing is a best-effort fallback
        return _parse_megacli_text(output)
    return _parse_storcli_json(output)


def _parse_megacli_text(output: str) -> list[RAIDArray]:
    """Minimal text parser for MegaCLI output (fallback)."""
    arrays: list[RAIDArray] = []
    current: Optional[RAIDArray] = None
    state = ControllerState.UNKNOWN

    for line in output.splitlines():
        if "Virtual Drive:" in line:
            if current is not None:
                arrays.append(current)
            current = RAIDArray(
                name=line.strip(),
                level="unknown",
                state=ControllerState.UNKNOWN,
                size_gb=0.0,
                free_gb=0.0,
            )
        elif current and "RAID Level" in line:
            current.level = line.split(":")[-1].strip()
        elif current and "State" in line and ":" in line:
            raw = line.split(":")[-1].strip().lower()
            if "optimal" in raw:
                current.state = ControllerState.OPTIMAL
            elif "degraded" in raw:
                current.state = ControllerState.DEGRADED
            else:
                current.state = ControllerState.FAILED

    if current is not None:
        arrays.append(current)
    return arrays


# ---------------------------------------------------------------------------
# mdadm backend (Linux software RAID)
# ---------------------------------------------------------------------------

def check_mdadm() -> list[RAIDArray]:
    """Parse /proc/mdstat for software RAID arrays."""
    arrays: list[RAIDArray] = []
    try:
        with open("/proc/mdstat") as f:
            content = f.read()
    except OSError:
        return arrays

    # Parse mdstat: each device block starts with "mdX :"
    blocks = re.split(r"\n(?=md\d)", content)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines or not lines[0].startswith("md"):
            continue

        header = lines[0]
        name_match = re.match(r"(md\d+)\s*:", header)
        if not name_match:
            continue
        name = name_match.group(1)

        # RAID level
        level_match = re.search(r"raid\d+|linear|multipath|faulty", header, re.IGNORECASE)
        level = level_match.group(0).upper() if level_match else "unknown"

        # State — "active" / "inactive" / "degraded" from second line
        state = ControllerState.OPTIMAL
        if "inactive" in header or len(lines) < 2:
            state = ControllerState.FAILED
        elif "_" in (lines[1] if len(lines) > 1 else ""):
            # Underscores in device list indicate failed/missing drives
            state = ControllerState.DEGRADED

        # Rebuild progress
        rebuild_pct: Optional[float] = None
        for line in lines:
            rb_match = re.search(r"=\s+([\d.]+)%", line)
            if rb_match:
                rebuild_pct = float(rb_match.group(1))

        # Array size
        size_gb = 0.0
        for line in lines:
            sz_match = re.search(r"(\d+)\s+blocks", line)
            if sz_match:
                size_gb = round(int(sz_match.group(1)) / (1024 * 1024), 2)

        arrays.append(RAIDArray(
            name=name,
            level=level,
            state=state,
            size_gb=size_gb,
            free_gb=0.0,
            rebuild_pct=rebuild_pct,
        ))

    return arrays


# ---------------------------------------------------------------------------
# Metric emission
# ---------------------------------------------------------------------------

STATE_TO_INT = {
    ControllerState.OPTIMAL: 0,
    ControllerState.DEGRADED: 1,
    ControllerState.FAILED: 2,
    ControllerState.UNKNOWN: 3,
}

DRIVE_STATE_TO_INT = {
    DriveState.ONLINE: 0,
    DriveState.HOTSPARE: 0,
    DriveState.UNCONFIGURED_GOOD: 0,
    DriveState.REBUILDING: 1,
    DriveState.UNKNOWN: 2,
    DriveState.UNCONFIGURED_BAD: 2,
    DriveState.FAILED: 3,
}


def emit_metrics(arrays: list[RAIDArray]) -> None:
    for array in arrays:
        tags = f"array:{array.name},level:{array.level}"
        print(f"METRIC|hardware.raid.state|{STATE_TO_INT[array.state]}|gauge|{tags}")
        print(f"METRIC|hardware.raid.size_gb|{array.size_gb:.1f}|gauge|{tags}")

        if array.rebuild_pct is not None:
            print(f"METRIC|hardware.raid.rebuild_pct|{array.rebuild_pct:.1f}|gauge|{tags}")

        failed_disks = sum(1 for d in array.disks if d.state == DriveState.FAILED)
        rebuilding_disks = sum(1 for d in array.disks if d.state == DriveState.REBUILDING)
        print(f"METRIC|hardware.raid.failed_disks|{failed_disks}|gauge|{tags}")
        print(f"METRIC|hardware.raid.rebuilding_disks|{rebuilding_disks}|gauge|{tags}")

        for disk in array.disks:
            dtags = f"{tags},enclosure:{disk.enclosure},slot:{disk.slot}"
            print(f"METRIC|hardware.raid.disk.state|{DRIVE_STATE_TO_INT[disk.state]}|gauge|{dtags}")
            print(f"METRIC|hardware.raid.disk.media_errors|{disk.media_errors}|gauge|{dtags}")
            print(f"METRIC|hardware.raid.disk.other_errors|{disk.other_errors}|gauge|{dtags}")

            if disk.state == DriveState.REBUILDING and disk.rebuild_pct is not None:
                print(f"METRIC|hardware.raid.disk.rebuild_pct|{disk.rebuild_pct:.1f}|gauge|{dtags}")
                print(
                    f"EVENT|RAID Disk Rebuilding: {array.name} slot {disk.slot}|"
                    f"Disk at enclosure {disk.enclosure} slot {disk.slot} is rebuilding: "
                    f"{disk.rebuild_pct:.1f}%|alert_type:warning|tags:{dtags}"
                )
            elif disk.state == DriveState.FAILED:
                print(
                    f"EVENT|RAID Disk FAILED: {array.name} slot {disk.slot}|"
                    f"Disk at enclosure {disk.enclosure} slot {disk.slot} has FAILED|"
                    f"alert_type:error|tags:{dtags}"
                )

            if disk.predictive_failure:
                print(
                    f"EVENT|RAID Disk Predictive Failure: slot {disk.slot}|"
                    f"Disk at slot {disk.slot} reports predictive failure — schedule replacement|"
                    f"alert_type:warning|tags:{dtags}"
                )

        if array.state == ControllerState.DEGRADED:
            print(
                f"EVENT|RAID Array DEGRADED: {array.name}|"
                f"Array {array.name} ({array.level}) is DEGRADED|"
                f"alert_type:error|tags:{tags}"
            )
        elif array.state == ControllerState.FAILED:
            print(
                f"EVENT|RAID Array FAILED: {array.name}|"
                f"Array {array.name} ({array.level}) has FAILED|"
                f"alert_type:error|tags:{tags}"
            )


def main() -> int:
    arrays: list[RAIDArray] = []

    # Try hardware RAID (storcli/MegaCLI) first
    hw_arrays = check_storcli()
    arrays.extend(hw_arrays)

    # Also check software RAID (mdadm)
    sw_arrays = check_mdadm()
    arrays.extend(sw_arrays)

    if not arrays:
        print("WARNING: No RAID arrays detected (no storcli/MegaCLI/mdstat)", file=sys.stderr)
        return 0  # Not an error if no RAID configured

    emit_metrics(arrays)

    failed = [a for a in arrays if a.state == ControllerState.FAILED]
    degraded = [a for a in arrays if a.state == ControllerState.DEGRADED]

    print(
        f"# RAID check: {len(arrays)} arrays, "
        f"{len(failed)} failed, {len(degraded)} degraded"
    )

    if failed:
        return 2
    if degraded:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
