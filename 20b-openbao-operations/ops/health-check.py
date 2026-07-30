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

"""Nagios-style health check for an OpenBao instance.

Queries:
    * sys/seal-status -- sealed cluster is CRITICAL
    * sys/health      -- standby on a single-node cluster is CRITICAL
    * audit device list -- no active devices is CRITICAL
    * file audit disk usage -- >90% is CRITICAL, >80% is WARNING

Exit codes follow Nagios conventions:
    0 = OK
    1 = WARNING
    2 = CRITICAL
    3 = UNKNOWN

Intended to be driven by Prometheus' blackbox exporter or a systemd timer.
Reads BAO_ADDR and BAO_TOKEN from the environment; the token only needs
`read` on `sys/health`, `sys/seal-status`, and `sys/audit`.
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from typing import Iterable

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3

BAO_ADDR = os.environ.get("BAO_ADDR", "http://127.0.0.1:18300")
BAO_TOKEN = os.environ.get("BAO_TOKEN", "")
AUDIT_PATH = os.environ.get("AUDIT_PATH", "/var/log/openbao/audit.log")
INSECURE = os.environ.get("BAO_SKIP_VERIFY", "") in ("1", "true", "True")


def _get(path: str, auth: bool) -> tuple[int, dict]:
    req = urllib.request.Request(BAO_ADDR + path)
    if auth and BAO_TOKEN:
        req.add_header("X-Vault-Token", BAO_TOKEN)
    ctx = ssl._create_unverified_context() if INSECURE else None  # noqa: SLF001
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {}
    except urllib.error.HTTPError as http_err:
        try:
            body = json.loads(http_err.read())
        except (json.JSONDecodeError, ValueError):
            body = {}
        return http_err.code, body
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def check_seal() -> tuple[int, str]:
    status, body = _get("/v1/sys/seal-status", auth=False)
    if status == 0:
        return CRITICAL, "CRITICAL - sys/seal-status unreachable"
    if body.get("sealed", True):
        return CRITICAL, f"CRITICAL - OpenBao is SEALED (cluster {body.get('cluster_name','?')})"
    return OK, f"OK - unsealed, version {body.get('version','?')}"


def check_health() -> tuple[int, str]:
    status, body = _get("/v1/sys/health", auth=False)
    # 200, 429, 472, 473, 501, 503 are all valid health responses; anything
    # else is an error we should surface. See the OpenBao API docs.
    valid = {200, 429, 472, 473, 501, 503}
    if status not in valid:
        return CRITICAL, f"CRITICAL - sys/health returned HTTP {status}"
    if body.get("sealed"):
        return CRITICAL, "CRITICAL - sys/health reports sealed"
    if body.get("standby"):
        return WARNING, "WARNING - this node is a standby"
    return OK, f"OK - version {body.get('version','?')}, cluster {body.get('cluster_name','?')}"


def check_audit_devices() -> tuple[int, str]:
    if not BAO_TOKEN:
        return UNKNOWN, "UNKNOWN - no BAO_TOKEN, cannot check audit devices"
    status, body = _get("/v1/sys/audit", auth=True)
    if status != 200:
        return CRITICAL, f"CRITICAL - sys/audit returned HTTP {status}"
    devices = body.get("data") or body
    enabled = [name for name in devices.keys() if not name.startswith("request_id")]
    if not enabled:
        return CRITICAL, "CRITICAL - no audit devices enabled (cluster will seal on next write)"
    if len(enabled) == 1:
        return WARNING, f"WARNING - only one audit device enabled: {enabled[0]}"
    return OK, f"OK - {len(enabled)} audit devices enabled: {', '.join(enabled)}"


def check_disk() -> tuple[int, str]:
    if not os.path.exists(AUDIT_PATH):
        return UNKNOWN, f"UNKNOWN - audit log not found at {AUDIT_PATH}"
    directory = os.path.dirname(AUDIT_PATH) or "/"
    try:
        usage = shutil.disk_usage(directory)
    except OSError as err:
        return UNKNOWN, f"UNKNOWN - disk_usage({directory}): {err}"
    pct = usage.used / usage.total * 100
    if pct >= 90:
        return CRITICAL, f"CRITICAL - audit volume {pct:.1f}% full"
    if pct >= 80:
        return WARNING, f"WARNING - audit volume {pct:.1f}% full"
    return OK, f"OK - audit volume {pct:.1f}% full"


def aggregate(results: Iterable[tuple[int, str]]) -> tuple[int, list[str]]:
    worst = OK
    messages: list[str] = []
    for code, msg in results:
        messages.append(msg)
        if code > worst:
            worst = code
    return worst, messages


def main() -> int:
    results = [
        check_seal(),
        check_health(),
        check_audit_devices(),
        check_disk(),
    ]
    worst, messages = aggregate(results)
    label = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}[worst]
    print(f"{label} - OpenBao health summary")
    for msg in messages:
        print(f"  {msg}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
