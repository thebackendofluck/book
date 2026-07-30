#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Network Traffic Encryption Monitor
Monitors network traffic to detect encrypted vs non-encrypted data transmission.
Integrates with Mikrotik RouterOS and Cisco Meraki switches for port mirroring.
Provides real-time monitoring dashboard and alerting.

Usage:
    python monitor.py --interface eth0
    python monitor.py --interface eth0 --mikrotik-host 192.168.1.1 --mikrotik-user admin --mikrotik-pass secret
    python monitor.py --interface eth0 --meraki-api-key YOUR_KEY --meraki-network-id YOUR_NET_ID
"""

import argparse
import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, Tuple

import requests
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit  # ty:ignore[unresolved-import]
from scapy.all import TCP, UDP, sniff, Raw  # ty:ignore[unresolved-import]
from scapy.layers.inet import IP  # ty:ignore[unresolved-import]
from scapy.layers.tls.all import TLS  # ty:ignore[unresolved-import]
import routeros_api  # ty:ignore[unresolved-import]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encryption detection constants
# ---------------------------------------------------------------------------
TLS_HANDSHAKE_BYTE = 0x16
DTLS_VERSION_PREFIX = b"\xfe\xfd"

ENCRYPTED_TCP_PORTS = {443, 993, 995, 465, 587}   # HTTPS, IMAPS, POP3S, SMTPS
PLAINTEXT_TCP_PORTS = {80, 21, 23, 25, 110, 143}   # HTTP, FTP, Telnet, SMTP, POP3, IMAP

HTTP_METHOD_PREFIXES = (b"GET ", b"POST ", b"PUT ", b"DELETE ", b"HEAD ", b"HTTP/")

# Alert thresholds
UNENCRYPTED_RATE_THRESHOLD = 0.50   # 50 %
HTTP_PACKET_ALERT_THRESHOLD = 100


class NetworkEncryptionMonitor:
    """Real-time network encryption monitor with switch integration and web dashboard."""

    def __init__(
        self,
        interface: str = "eth0",
        mikrotik_config: Dict = None,  # ty:ignore[invalid-parameter-default]
        meraki_config: Dict = None,  # ty:ignore[invalid-parameter-default]
    ):
        self.interface = interface
        self.mikrotik_config = mikrotik_config or {}
        self.meraki_config = meraki_config or {}

        # Packet statistics
        self.stats = {
            "total_packets": 0,
            "encrypted_packets": 0,
            "unencrypted_packets": 0,
            "encrypted_bytes": 0,
            "unencrypted_bytes": 0,
            "protocol_breakdown": defaultdict(int),
            "encryption_trends": deque(maxlen=100),
            "alerts": [],
        }

        # Flask + SocketIO dashboard
        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        self._setup_routes()

        # Control flags
        self.running = False
        self._monitor_thread = None

    # ------------------------------------------------------------------
    # Dashboard routes
    # ------------------------------------------------------------------
    def _setup_routes(self):
        @self.app.route("/")
        def dashboard():
            return render_template_string(DASHBOARD_HTML)

        @self.socketio.on("connect")
        def handle_connect():
            logger.info("Dashboard client connected")
            emit("stats_update", self.get_stats())

    # ------------------------------------------------------------------
    # Switch integration -- Mikrotik RouterOS API
    # ------------------------------------------------------------------
    def configure_mikrotik_mirroring(
        self, source_interface: str, target_interface: str
    ) -> bool:
        """Configure port mirroring on a Mikrotik switch via RouterOS API."""
        try:
            if not self.mikrotik_config:
                logger.warning("Mikrotik config not provided")
                return False

            pool = routeros_api.RouterOsApiPool(
                host=self.mikrotik_config["host"],
                username=self.mikrotik_config["username"],
                password=self.mikrotik_config["password"],
                port=self.mikrotik_config.get("port", 8728),
            )
            api = pool.get_api()

            mirror_rule = {
                "name": "traffic-mirror",
                "mirror-source": source_interface,
                "mirror-target": target_interface,
                "comment": "Encryption monitoring mirror",
            }
            api.get_resource("/interface/ethernet").set(
                id=source_interface, **mirror_rule
            )
            logger.info(
                "Mikrotik mirroring configured: %s -> %s",
                source_interface,
                target_interface,
            )
            return True

        except Exception as e:
            logger.error("Failed to configure Mikrotik mirroring: %s", e)
            return False

    # ------------------------------------------------------------------
    # Switch integration -- Cisco Meraki Dashboard API
    # ------------------------------------------------------------------
    def configure_meraki_mirroring(
        self, network_id: str, source_port: str, target_port: str
    ) -> bool:
        """Configure port mirroring on a Cisco Meraki switch via Dashboard API."""
        try:
            if not self.meraki_config:
                logger.warning("Meraki config not provided")
                return False

            base_url = "https://api.meraki.com/api/v1"
            headers = {
                "X-Cisco-Meraki-API-Key": self.meraki_config["api_key"],
                "Content-Type": "application/json",
            }

            # Retrieve switch ports
            ports_url = f"{base_url}/networks/{network_id}/switch/ports"
            response = requests.get(ports_url, headers=headers, timeout=30)
            response.raise_for_status()
            ports = response.json()

            source_id = target_id = None
            for port in ports:
                if port["portId"] == source_port:
                    source_id = port["portId"]
                if port["portId"] == target_port:
                    target_id = port["portId"]

            if not source_id or not target_id:
                logger.error("Source or target port not found on Meraki switch")
                return False

            mirror_config = {
                "mirror": {
                    "mode": "mirror",
                    "sourcePort": source_id,
                    "targetPort": target_id,
                }
            }
            update_url = f"{base_url}/networks/{network_id}/switch/ports/{source_id}"
            response = requests.put(
                update_url, headers=headers, json=mirror_config, timeout=30
            )
            response.raise_for_status()

            logger.info(
                "Meraki mirroring configured: port %s -> port %s",
                source_port,
                target_port,
            )
            return True

        except Exception as e:
            logger.error("Failed to configure Meraki mirroring: %s", e)
            return False

    # ------------------------------------------------------------------
    # Deep packet inspection -- encryption detection
    # ------------------------------------------------------------------
    def is_packet_encrypted(self, packet) -> Tuple[bool, str]:
        """Determine whether a captured packet carries encrypted data.

        Detection strategy (in order):
        1. Scapy TLS layer dissection
        2. TCP port 443 + TLS handshake byte (0x16) in payload
        3. Known encrypted-protocol ports (IMAPS, POP3S, SMTPS)
        4. DTLS detection on UDP/443 via version prefix
        5. Plain-text HTTP method detection on port 80
        6. Known plain-text protocol ports (FTP, Telnet, SMTP, POP3, IMAP)
        7. Default: classify as encrypted (conservative for compliance)
        """
        try:
            if not packet.haslayer(IP):
                return False, "non-ip"

            # --- TLS layer detected by Scapy ---
            if packet.haslayer(TLS):
                return True, "tls"

            # --- HTTPS / TLS on port 443 ---
            if packet.haslayer(TCP) and (
                packet[TCP].dport == 443 or packet[TCP].sport == 443
            ):
                if packet.haslayer(Raw):
                    payload = packet[Raw].load
                    if len(payload) >= 5 and payload[0] == TLS_HANDSHAKE_BYTE:
                        return True, "https"
                # Port 443 traffic without visible payload -- assume encrypted
                return True, "https"

            # --- Other encrypted TCP protocols ---
            if packet.haslayer(TCP) and packet[TCP].dport in ENCRYPTED_TCP_PORTS:
                return True, "secure_mail"

            # --- DTLS on UDP/443 ---
            if packet.haslayer(UDP) and packet[UDP].dport == 443:
                if packet.haslayer(Raw):
                    payload = packet[Raw].load
                    if (
                        len(payload) >= 13
                        and payload[0] == TLS_HANDSHAKE_BYTE
                        and payload[1:3] == DTLS_VERSION_PREFIX
                    ):
                        return True, "dtls"

            # --- Plain-text HTTP ---
            if packet.haslayer(TCP) and (
                packet[TCP].dport == 80 or packet[TCP].sport == 80
            ):
                if packet.haslayer(Raw):
                    payload = packet[Raw].load
                    if payload.startswith(HTTP_METHOD_PREFIXES):
                        return False, "http"

            # --- Other plain-text protocols ---
            if packet.haslayer(TCP) and packet[TCP].dport in PLAINTEXT_TCP_PORTS:
                return False, "plain_text_protocol"

            # Conservative default: assume encrypted
            return True, "unknown_encrypted"

        except Exception as e:
            logger.error("Error analysing packet: %s", e)
            return False, "error"

    # ------------------------------------------------------------------
    # Packet processing pipeline
    # ------------------------------------------------------------------
    def packet_callback(self, packet):
        """Process each captured packet -- classify, aggregate, alert."""
        self.stats["total_packets"] += 1  # ty:ignore[unsupported-operator]

        encrypted, protocol = self.is_packet_encrypted(packet)

        if encrypted:
            self.stats["encrypted_packets"] += 1  # ty:ignore[unsupported-operator]
            self.stats["encrypted_bytes"] += len(packet)  # ty:ignore[unsupported-operator]
        else:
            self.stats["unencrypted_packets"] += 1  # ty:ignore[unsupported-operator]
            self.stats["unencrypted_bytes"] += len(packet)  # ty:ignore[unsupported-operator]

        self.stats["protocol_breakdown"][protocol] += 1  # ty:ignore[invalid-argument-type]

        self.stats["encryption_trends"].append(  # ty:ignore[unresolved-attribute]
            {
                "timestamp": datetime.now().isoformat(),
                "encrypted": self.stats["encrypted_packets"],
                "unencrypted": self.stats["unencrypted_packets"],
            }
        )

        self._check_alerts()

        # Push dashboard update every 10 packets
        if self.stats["total_packets"] % 10 == 0:  # ty:ignore[unsupported-operator]
            self.socketio.emit("stats_update", self.get_stats())

    def _check_alerts(self):
        """Evaluate alert conditions against current statistics."""
        total = self.stats["total_packets"]
        if total == 0:
            return

        unencrypted_rate = self.stats["unencrypted_packets"] / total  # ty:ignore[unsupported-operator]

        if unencrypted_rate > UNENCRYPTED_RATE_THRESHOLD:
            self.stats["alerts"].append(  # ty:ignore[unresolved-attribute]
                {
                    "timestamp": datetime.now().isoformat(),
                    "severity": "HIGH",
                    "message": (
                        f"Unencrypted traffic rate {unencrypted_rate:.1%} "
                        f"exceeds {UNENCRYPTED_RATE_THRESHOLD:.0%} threshold"
                    ),
                    "recommendation": (
                        "Review network configuration and enforce encryption policies"
                    ),
                }
            )
            logger.warning(
                "HIGH ALERT: %.1f%% unencrypted traffic", unencrypted_rate * 100
            )

        if self.stats["protocol_breakdown"]["http"] > HTTP_PACKET_ALERT_THRESHOLD:  # ty:ignore[invalid-argument-type, non-subscriptable]
            self.stats["alerts"].append(  # ty:ignore[unresolved-attribute]
                {
                    "timestamp": datetime.now().isoformat(),
                    "severity": "MEDIUM",
                    "message": (
                        f'{self.stats["protocol_breakdown"]["http"]} HTTP packets '  # ty:ignore[invalid-argument-type, non-subscriptable]
                        f"detected -- enforce HTTPS with HSTS"
                    ),
                    "recommendation": "Implement HSTS and redirect HTTP to HTTPS",
                }
            )

    def get_stats(self) -> Dict:
        """Return a JSON-serialisable snapshot of current statistics."""
        total = self.stats["total_packets"]
        return {
            "total_packets": total,
            "encrypted_packets": self.stats["encrypted_packets"],
            "unencrypted_packets": self.stats["unencrypted_packets"],
            "encrypted_bytes": self.stats["encrypted_bytes"],
            "unencrypted_bytes": self.stats["unencrypted_bytes"],
            "encryption_rate": (
                (self.stats["encrypted_packets"] / total * 100) if total > 0 else 0  # ty:ignore[unsupported-operator]
            ),
            "protocol_breakdown": dict(self.stats["protocol_breakdown"]),  # ty:ignore[no-matching-overload]
            "encryption_trends": list(self.stats["encryption_trends"]),  # ty:ignore[invalid-argument-type]
            "alerts": self.stats["alerts"][-10:],  # ty:ignore[invalid-argument-type, non-subscriptable]
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_monitoring(self):
        """Configure switches and begin packet capture."""
        logger.info("Starting network encryption monitoring on %s", self.interface)

        if self.mikrotik_config:
            self.configure_mikrotik_mirroring(
                self.mikrotik_config.get("source_interface", "ether1"),
                self.mikrotik_config.get("target_interface", "ether2"),
            )

        if self.meraki_config:
            self.configure_meraki_mirroring(
                self.meraki_config["network_id"],
                self.meraki_config.get("source_port", "1"),
                self.meraki_config.get("target_port", "2"),
            )

        self.running = True
        sniff(
            iface=self.interface,
            prn=self.packet_callback,
            store=0,
            stop_filter=lambda _: not self.running,
        )

    def start(self):
        """Launch monitoring thread and web dashboard."""
        self._monitor_thread = threading.Thread(target=self.start_monitoring, daemon=True)
        self._monitor_thread.start()

        logger.info("Dashboard available at http://127.0.0.1:5000")
        self.socketio.run(self.app, host="127.0.0.1", port=5000, debug=False)

    def stop(self):
        """Gracefully stop monitoring."""
        self.running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Monitoring stopped")


# ---------------------------------------------------------------------------
# Minimal dashboard HTML (served inline for single-file deployment)
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Network Encryption Monitor</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        h1 { color: #333; }
        .stats { display: flex; flex-wrap: wrap; gap: 20px; }
        .stat-card {
            background: #fff; border: 1px solid #ddd; padding: 15px;
            border-radius: 5px; min-width: 200px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
        }
        .alerts { margin-top: 30px; }
        .alert { padding: 10px; margin: 5px 0; border-radius: 3px; }
        .alert-high { background: #ffebee; border-left: 4px solid #f44336; }
        .alert-medium { background: #fff3e0; border-left: 4px solid #ff9800; }
        .chart { margin-top: 20px; }
    </style>
</head>
<body>
    <h1>Network Encryption Monitor</h1>
    <div class="stats">
        <div class="stat-card"><h3>Total Packets</h3><p id="stat-total">0</p></div>
        <div class="stat-card"><h3>Encrypted</h3><p id="stat-encrypted">0</p></div>
        <div class="stat-card"><h3>Unencrypted</h3><p id="stat-unencrypted">0</p></div>
        <div class="stat-card"><h3>Encryption Rate</h3><p id="stat-rate">0%</p></div>
    </div>
    <div class="chart">
        <h2>Encryption Trend (last 100 samples)</h2>
        <canvas id="trendChart" width="800" height="200"></canvas>
    </div>
    <div class="alerts"><h2>Recent Alerts</h2><div id="alerts"></div></div>

    <script>
        const socket = io();
        socket.on('stats_update', function(data) {
            document.getElementById('stat-total').textContent = data.total_packets;
            document.getElementById('stat-encrypted').textContent =
                data.encrypted_packets + ' (' + data.encrypted_bytes + ' bytes)';
            document.getElementById('stat-unencrypted').textContent =
                data.unencrypted_packets + ' (' + data.unencrypted_bytes + ' bytes)';
            const rate = data.total_packets > 0
                ? ((data.encrypted_packets / data.total_packets) * 100).toFixed(1) : 0;
            document.getElementById('stat-rate').textContent = rate + '%';

            // Render trend chart
            const canvas = document.getElementById('trendChart');
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const trends = data.encryption_trends;
            if (trends.length > 0) {
                const barW = canvas.width / trends.length;
                trends.forEach((pt, i) => {
                    const total = pt.encrypted + pt.unencrypted;
                    const h = total > 0 ? (pt.encrypted / total) * canvas.height : 0;
                    ctx.fillStyle = '#4CAF50';
                    ctx.fillRect(i * barW, canvas.height - h, barW - 1, h);
                });
            }

            // Render alerts
            const ad = document.getElementById('alerts');
            ad.innerHTML = '';
            data.alerts.slice(-10).reverse().forEach(function(a) {
                const div = document.createElement('div');
                div.className = 'alert alert-' + a.severity.toLowerCase();
                div.innerHTML = '<strong>' + a.timestamp + '</strong>: ' + a.message;
                ad.appendChild(div);
            });
        });
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Network Traffic Encryption Monitor")
    parser.add_argument("--interface", default="eth0", help="Network interface to monitor")
    parser.add_argument("--mikrotik-host", help="Mikrotik router IP address")
    parser.add_argument("--mikrotik-user", help="Mikrotik API username")
    parser.add_argument("--mikrotik-pass", help="Mikrotik API password")
    parser.add_argument("--meraki-api-key", help="Cisco Meraki Dashboard API key")
    parser.add_argument("--meraki-network-id", help="Meraki network ID")
    args = parser.parse_args()

    mikrotik_config = None
    if args.mikrotik_host:
        mikrotik_config = {
            "host": args.mikrotik_host,
            "username": args.mikrotik_user,
            "password": args.mikrotik_pass,
            "source_interface": "ether1",
            "target_interface": "ether2",
        }

    meraki_config = None
    if args.meraki_api_key:
        meraki_config = {
            "api_key": args.meraki_api_key,
            "network_id": args.meraki_network_id,
            "source_port": "1",
            "target_port": "2",
        }

    monitor = NetworkEncryptionMonitor(
        interface=args.interface,
        mikrotik_config=mikrotik_config,  # ty:ignore[invalid-argument-type]
        meraki_config=meraki_config,  # ty:ignore[invalid-argument-type]
    )

    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()


if __name__ == "__main__":
    main()
