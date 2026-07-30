#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""HSM health collector -- aggregates YubiHSM, OpenBao proxy, service and
traffic-bot state into Redis on a 30-second cadence.

This is the reference implementation of the collector described in chapter
20b, matching the two-key layout used by the production collector at
/opt/hsm-collector.py on the ops-host host. The production code path writes:

    hsm:health           -- hot key, SET with 120s TTL, full status JSON
    hsm:health_history   -- ZADD ring buffer capped at 2880 entries (24h at 30s)

Both keys are read by the HSM Security panel of the operator dashboard at
https://new.acmetocasino.com/dashboard.html.

Design goals:
    * No external dependencies beyond the Python standard library.
    * Fail soft -- any single probe failing must not break the whole cycle.
    * Write a TTL-bounded hot key so that a stalled collector is detectable
      via a key expiry (the dashboard treats TTL==-2 as "collector down").
    * Maintain a 24h sorted-set ring buffer for the dashboard sparklines.
    * Include a direct yubihsm-shell probe for firmware/serial verification
      so that compliance reviewers can confirm hardware identity.

The collector is expected to run under a systemd unit with Restart=always;
there is no internal supervisor loop beyond the outer while-True.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

INTERVAL_SECONDS = int(os.environ.get("COLLECTOR_INTERVAL", "30"))
HOT_KEY_TTL = INTERVAL_SECONDS * 4  # four missed cycles = dead
RING_BUFFER_MAX = 2880  # 24h at 30s resolution
REDIS_PORT = os.environ.get("REDIS_PORT", "6382")
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
CONNECTOR_URL = os.environ.get("CONNECTOR_URL", "http://127.0.0.1:12345/connector/status")
PROXY_HEALTH_URL = os.environ.get("PROXY_HEALTH_URL", "http://127.0.0.1:8190/health")
PROXY_API_KEY = os.environ.get("HSM_PROXY_API_KEY", "")
MONITORED_SERVICES = ("yubihsm-connector", "hsm-proxy-api", "hsm-traffic-bot")


def redis_cmd(*args: str) -> str:
    """Run redis-cli against the target Redis and return stdout stripped.

    Any failure (non-zero exit, timeout, redis-cli missing) returns an
    empty string. The caller decides how to interpret that.
    """
    try:
        proc = subprocess.run(
            ["redis-cli", "-h", REDIS_HOST, "-p", REDIS_PORT, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def redis_set(key: str, value: Any, ttl: int = HOT_KEY_TTL) -> None:
    payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    redis_cmd("SET", key, payload, "EX", str(ttl))


def redis_zadd(key: str, value: dict, max_entries: int = RING_BUFFER_MAX) -> None:
    score = str(int(time.time() * 1000))
    redis_cmd("ZADD", key, score, json.dumps(value))
    # Trim oldest entries beyond max_entries. ZREMRANGEBYRANK indices are
    # inclusive, so "0 -(max+1)" removes everything below the threshold.
    redis_cmd("ZREMRANGEBYRANK", key, "0", str(-max_entries - 1))


@dataclass
class ConnectorState:
    up: bool = False
    serial: str = "?"
    version: str = "?"
    pid: str = "?"


@dataclass
class ProxyState:
    up: bool = False
    http_code: int = 0


@dataclass
class UsbState:
    connected: bool = False
    device: str = ""


@dataclass
class BotState:
    ops: int = 0
    errors: int = -1  # -1 == "unknown"
    mtls: str = "UNKNOWN"


@dataclass
class DeviceState:
    """Direct probe of the YubiHSM via yubihsm-shell get-device-info.

    Returns firmware version, serial number and audit log usage, which
    a compliance reviewer can verify against the hardware inventory.
    """
    reachable: bool = False
    firmware: str = "?"
    serial: str = "?"
    log_used: str = "?"


@dataclass
class HealthStatus:
    """Aggregated snapshot matching the production /opt/hsm-collector.py
    payload shape. Stored under the `hsm:health` hot key."""
    status: str  # "healthy" or "degraded"
    timestamp: str
    connector: ConnectorState = field(default_factory=ConnectorState)
    proxy_api: ProxyState = field(default_factory=ProxyState)
    usb: UsbState = field(default_factory=UsbState)
    services: dict[str, bool] = field(default_factory=dict)
    traffic_bot: BotState = field(default_factory=BotState)
    device: DeviceState = field(default_factory=DeviceState)


def check_connector() -> ConnectorState:
    """Query the yubihsm-connector's /connector/status endpoint.

    The response is a simple key=value text block; we parse it defensively
    because the format has changed between Yubico releases in the past.
    """
    try:
        with urllib.request.urlopen(CONNECTOR_URL, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        parts: dict[str, str] = {}
        for line in body.strip().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                parts[key.strip()] = value.strip()
        return ConnectorState(
            up=parts.get("status", "") == "OK",
            serial=parts.get("serial", "?"),
            version=parts.get("version", "?"),
            pid=parts.get("pid", "?"),
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return ConnectorState(up=False)


def check_proxy() -> ProxyState:
    """Query the hsm-proxy-api /health endpoint with the shared API key."""
    req = urllib.request.Request(PROXY_HEALTH_URL)
    if PROXY_API_KEY:
        req.add_header("X-API-Key", PROXY_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return ProxyState(up=resp.status == 200, http_code=resp.status)
    except urllib.error.HTTPError as http_err:
        # 4xx with an API key implies misconfiguration, not outage. The
        # dashboard treats <500 as "up (auth failing)" rather than P1 down.
        return ProxyState(up=http_err.code < 500, http_code=http_err.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return ProxyState(up=False, http_code=0)


def check_usb() -> UsbState:
    """Run `lsusb` and look for a line containing 'Yubi' (case-insensitive)."""
    try:
        proc = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5, check=False)
        for line in proc.stdout.splitlines():
            if "yubi" in line.lower():
                return UsbState(connected=True, device=line.strip())
        return UsbState()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return UsbState()


def check_services() -> dict[str, bool]:
    """Check `systemctl is-active` for each monitored unit."""
    result: dict[str, bool] = {}
    for svc in MONITORED_SERVICES:
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            result[svc] = proc.stdout.strip() == "active"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result[svc] = False
    return result


def check_bot_health() -> BotState:
    """Parse the most recent hsm-traffic-bot journal line for ops/errors."""
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "hsm-traffic-bot", "--no-pager", "-n", "1", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return BotState()

    state = BotState()
    for token in proc.stdout.strip().split():
        if token.startswith("ops:"):
            try:
                state.ops = int(token.partition(":")[2])
            except ValueError:
                pass
        elif token.startswith("errs:"):
            try:
                state.errors = int(token.partition(":")[2])
            except ValueError:
                pass
        elif token.startswith("mtls:"):
            state.mtls = token.partition(":")[2]
    return state


def check_hsm_device() -> DeviceState:
    """Query the YubiHSM directly via yubihsm-shell get-device-info.

    This is the probe that gives the dashboard firmware version, serial
    number and audit log usage. It is also the only probe that reads
    information from the HSM hardware itself rather than the surrounding
    daemons, which makes it the most authoritative health signal.
    """
    try:
        proc = subprocess.run(
            ["yubihsm-shell", "-a", "get-device-info", "-C", "http://127.0.0.1:12345"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return DeviceState()

    info: dict[str, str] = {}
    for line in proc.stdout.strip().splitlines():
        if ":\t" in line:
            key, _, value = line.partition("\t")
            normalised = key.strip().rstrip(":").lower().replace(" ", "_")
            info[normalised] = value.strip()

    if not info:
        return DeviceState()

    return DeviceState(
        reachable=True,
        firmware=info.get("version_number", "?"),
        serial=info.get("serial_number", "?"),
        log_used=info.get("log_used", "?"),
    )


def compute_aggregate_status(
    connector: ConnectorState,
    usb: UsbState,
    device: DeviceState,
    services: dict[str, bool],
    bot: BotState,
) -> str:
    """Return 'healthy' iff every probe is in its good state, otherwise 'degraded'.

    The conservative definition mirrors the production collector: any
    failing service, any error from the traffic bot, or a missing hardware
    probe drops the overall status. This is deliberately brittle -- a
    dashboard that reports 'healthy' under any ambiguity is worse than
    useless.
    """
    all_ok = (
        connector.up
        and usb.connected
        and device.reachable
        and all(services.values())
        and bot.mtls == "OK"
        and bot.errors == 0
    )
    return "healthy" if all_ok else "degraded"


def collect_once() -> HealthStatus:
    now_iso = datetime.now(timezone.utc).isoformat()
    connector = check_connector()
    proxy = check_proxy()
    usb = check_usb()
    services = check_services()
    bot = check_bot_health()
    device = check_hsm_device()
    status = compute_aggregate_status(connector, usb, device, services, bot)
    return HealthStatus(
        status=status,
        timestamp=now_iso,
        connector=connector,
        proxy_api=proxy,
        usb=usb,
        services=services,
        traffic_bot=bot,
        device=device,
    )


def publish(snapshot: HealthStatus) -> None:
    # Hot key: full snapshot under `hsm:health`. TTL of HOT_KEY_TTL lets the
    # dashboard detect collector death without a separate heartbeat.
    redis_set("hsm:health", asdict(snapshot))

    # 24h history ring buffer. Only the scalar fields most useful to the
    # dashboard sparklines go in here, to keep each entry small.
    redis_zadd("hsm:health_history", {
        "status": snapshot.status,
        "timestamp": snapshot.timestamp,
        "ops": snapshot.traffic_bot.ops,
        "errors": snapshot.traffic_bot.errors,
        "mtls": snapshot.traffic_bot.mtls,
        "connector_up": snapshot.connector.up,
        "device_reachable": snapshot.device.reachable,
    })


def main() -> int:
    print(f"HSM Health Collector started (interval={INTERVAL_SECONDS}s)", flush=True)
    while True:
        try:
            snapshot = collect_once()
            publish(snapshot)
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] {snapshot.status} "
                f"| ops:{snapshot.traffic_bot.ops} "
                f"errs:{snapshot.traffic_bot.errors} "
                f"mtls:{snapshot.traffic_bot.mtls}",
                flush=True,
            )
        except Exception as err:  # noqa: BLE001 -- keep the loop alive under any failure
            sys.stderr.write(f"[hsm-collector] cycle failed: {err}\n")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
