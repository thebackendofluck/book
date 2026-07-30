#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
blocklist-server.py — Serve filtered blocklists over HTTP for OPNsense URL table aliases.

Listens on localhost:8765 by default. Bind to a management VLAN interface
for access from OPNsense. Never expose this to the public internet.

The files come from iprep_update.py, which writes them into the same directory
on every update cycle (BLOCKLIST_SERVE_DIR must agree between the two).

Endpoints:
  GET /blocklist/recommended   — Data-Shield RECOMMENDED list, whitelist-filtered
  GET /blocklist/aggressive    — Data-Shield AGGRESSIVE list, whitelist-filtered
  GET /blocklist/combined      — All sources merged, CIDR format
  GET /status                  — JSON status; 200 when every list is present and
                                 fresh, 503 otherwise, so a monitor notices a
                                 stalled pipeline instead of reading a 200 and
                                 assuming all is well
"""

import http.server
import json
import logging
import os
import socketserver
from datetime import datetime, timedelta, timezone
from pathlib import Path

SERVE_DIR = Path(os.environ.get("BLOCKLIST_SERVE_DIR", "/var/lib/iprep/serve"))
BIND_HOST = os.environ.get("BLOCKLIST_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("BLOCKLIST_BIND_PORT", "8765"))

# iprep-update.timer runs every 4 hours. Anything older than twice that means
# updates have stopped, even though the files on disk still look fine.
STALE_AFTER = timedelta(hours=int(os.environ.get("BLOCKLIST_STALE_AFTER_HOURS", "8")))

BLOCKLIST_FILES = {
    "recommended": "recommended.txt",
    "aggressive": "aggressive.txt",
    "combined": "combined.txt",
}

log = logging.getLogger("blocklist-server")


def count_entries(filepath: Path) -> int:
    with filepath.open() as fh:
        return sum(1 for line in fh if line.strip() and not line.startswith('#'))


class BlocklistHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path_map = {
            f"/blocklist/{name}": SERVE_DIR / filename
            for name, filename in BLOCKLIST_FILES.items()
        }

        if self.path in path_map:
            filepath = path_map[self.path]
            if filepath.exists():
                content = filepath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(content)
            else:
                # Deliberately an error rather than an empty body: OPNsense
                # keeps the previous alias table on a failed fetch, but would
                # flush it if we served zero entries with a 200.
                self.send_error(503, f"Blocklist file not available: {filepath.name}")

        elif self.path == "/status":
            self.send_status()

        else:
            self.send_error(404)

    def send_status(self):
        now = datetime.now(timezone.utc)
        status = {}
        problems = []

        for name, filename in BLOCKLIST_FILES.items():
            filepath = SERVE_DIR / filename
            if not filepath.exists():
                status[name] = {"available": False, "reason": "file not written yet"}
                problems.append(f"{name}: missing")
                continue

            mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            entry_count = count_entries(filepath)
            stale = age > STALE_AFTER
            status[name] = {
                "available": True,
                "last_updated": mtime.isoformat(),
                "age_seconds": int(age.total_seconds()),
                "entry_count": entry_count,
                "stale": stale,
            }
            if stale:
                problems.append(f"{name}: stale by {age - STALE_AFTER}")
            if entry_count == 0:
                problems.append(f"{name}: zero entries")

        healthy = not problems
        body = json.dumps(
            {"healthy": healthy, "problems": problems, "lists": status},
            indent=2,
        ).encode()

        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        log.debug(f"{self.client_address[0]} - {format % args}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    SERVE_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Serving blocklists from {SERVE_DIR} on {BIND_HOST}:{BIND_PORT}")
    with socketserver.TCPServer((BIND_HOST, BIND_PORT), BlocklistHandler) as httpd:
        httpd.serve_forever()
