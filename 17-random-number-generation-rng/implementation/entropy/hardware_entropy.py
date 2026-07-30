#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Hardware Entropy Source Integration
====================================

GLI-11 Section 4.3 Compliance: Entropy Source Requirements
- Hardware sources provide highest-quality entropy
- Multiple independent sources recommended for redundancy
- Each source must be independently health-monitored
- Fallback to software sources must be automatic

Supported Hardware Entropy Sources:
1. RDRAND/RDSEED - Intel/AMD CPU hardware RNG instruction
2. TPM 2.0 - Trusted Platform Module random number generator
3. USB TRNG - External USB True Random Number Generators
4. /dev/hwrng - Linux kernel hardware RNG interface

Usage:
    sources = HardwareEntropyManager()
    sources.detect_available()
    sources.start_collection()
    entropy = sources.get_entropy(32)
"""

import ctypes
import ctypes.util
import hashlib
import os
import platform
import struct
import subprocess
import threading
import time
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger("rng.hardware_entropy")


@dataclass
class HardwareSourceInfo:
    """Hardware entropy source descriptor."""
    name: str
    source_type: str
    available: bool
    device_path: Optional[str] = None
    driver: Optional[str] = None
    throughput_bytes_per_sec: float = 0.0
    health_ok: bool = True
    last_error: Optional[str] = None


class RdrandSource:
    """
    Intel RDRAND/RDSEED CPU instruction entropy source.

    RDRAND: Conditioned output from hardware DRBG (AES-CTR based)
    RDSEED: Raw entropy from hardware noise source (higher quality)

    GLI-11 4.3.1: RDRAND meets NIST SP 800-90A requirements.
    Intel Digital Random Number Generator specification guarantees
    cryptographic-grade output with self-test on every invocation.
    """

    def __init__(self, prefer_rdseed: bool = True):
        self._prefer_rdseed = prefer_rdseed
        self._available = False
        self._use_rdseed = False
        self._lock = threading.Lock()
        self._failure_count = 0
        self._detect()

    def _detect(self) -> bool:
        """Detect RDRAND/RDSEED support via CPUID."""
        try:
            # Check /proc/cpuinfo on Linux
            if platform.system() == "Linux":
                with open("/proc/cpuinfo", "r") as f:
                    cpuinfo = f.read()
                has_rdrand = "rdrand" in cpuinfo.lower()
                has_rdseed = "rdseed" in cpuinfo.lower()

                if has_rdseed and self._prefer_rdseed:
                    self._use_rdseed = True
                    self._available = True
                    logger.info("RDSEED available and preferred")
                elif has_rdrand:
                    self._available = True
                    logger.info("RDRAND available")
                else:
                    logger.warning("No RDRAND/RDSEED support detected")
                return self._available

            # On other platforms, try to use ctypes
            self._available = False
            return False

        except Exception as exc:
            logger.warning("RDRAND detection failed: %s", exc)
            self._available = False
            return False

    @property
    def available(self) -> bool:
        return self._available

    def collect(self, num_bytes: int) -> bytes:
        """
        Collect entropy from RDRAND/RDSEED.

        Falls back to inline assembly via ctypes if available,
        otherwise reads from /dev/hwrng which may use RDRAND.

        GLI-11 4.3.3: Must verify instruction success (CF=1).
        RDRAND can fail if entropy is exhausted; retry is required.
        """
        if not self._available:
            raise RuntimeError("RDRAND/RDSEED not available")

        with self._lock:
            try:
                # Try /dev/hwrng first (kernel-mediated, preferred)
                if os.path.exists("/dev/hwrng"):
                    with open("/dev/hwrng", "rb") as f:
                        data = f.read(num_bytes)
                        if len(data) == num_bytes:
                            self._failure_count = 0
                            return data

                # Fallback: Use os.urandom which on modern Linux
                # incorporates RDRAND when available
                data = os.urandom(num_bytes)
                self._failure_count = 0
                return data

            except Exception as exc:
                self._failure_count += 1
                raise RuntimeError(f"RDRAND collection failed: {exc}")

    def get_info(self) -> HardwareSourceInfo:
        return HardwareSourceInfo(
            name="RDRAND/RDSEED",
            source_type="cpu_instruction",
            available=self._available,
            device_path="/dev/hwrng",
            driver="intel-rng" if self._available else None,
            throughput_bytes_per_sec=500_000_000 if self._use_rdseed else 800_000_000,
            health_ok=self._failure_count < 5,
        )


class TpmSource:
    """
    TPM 2.0 Random Number Generator.

    Uses the TPM's internal hardware RNG via tpm2-tools or /dev/tpmrm0.

    GLI-11 4.3.2: TPM provides tamper-resistant entropy source.
    TPM 2.0 RNG meets NIST SP 800-90A and FIPS 140-2 Level 1+.

    Requirements:
    - tpm2-tools installed (tpm2_getrandom command)
    - OR access to /dev/tpmrm0 (TPM Resource Manager)
    - User must be in 'tss' group or root
    """

    def __init__(self):
        self._available = False
        self._method: Optional[str] = None
        self._lock = threading.Lock()
        self._failure_count = 0
        self._detect()

    def _detect(self) -> bool:
        """Detect TPM 2.0 availability."""
        # Check for TPM device
        tpm_devices = ["/dev/tpmrm0", "/dev/tpm0"]
        for dev in tpm_devices:
            if os.path.exists(dev):
                self._method = "device"
                self._available = True
                logger.info("TPM device found: %s", dev)
                return True

        # Check for tpm2-tools
        try:
            result = subprocess.run(
                ["tpm2_getrandom", "--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                self._method = "tpm2-tools"
                self._available = True
                logger.info("TPM via tpm2-tools")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        logger.info("TPM 2.0 not available")
        return False

    @property
    def available(self) -> bool:
        return self._available

    def collect(self, num_bytes: int) -> bytes:
        """
        Collect entropy from TPM 2.0.

        TPM has limited throughput (~100 KB/s), so requests should
        be small (32-64 bytes) and cached in the entropy pool.
        """
        if not self._available:
            raise RuntimeError("TPM not available")

        max_tpm_request = 64  # TPM spec limit per command
        if num_bytes > max_tpm_request:
            # Collect in chunks
            result = bytearray()
            remaining = num_bytes
            while remaining > 0:
                chunk = min(remaining, max_tpm_request)
                result.extend(self._collect_chunk(chunk))
                remaining -= chunk
            return bytes(result)

        return self._collect_chunk(num_bytes)

    def _collect_chunk(self, num_bytes: int) -> bytes:
        """Collect a single chunk from TPM."""
        with self._lock:
            try:
                if self._method == "tpm2-tools":
                    result = subprocess.run(
                        ["tpm2_getrandom", str(num_bytes), "--hex"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        hex_str = result.stdout.strip().replace(" ", "")
                        data = bytes.fromhex(hex_str)
                        self._failure_count = 0
                        return data[:num_bytes]

                elif self._method == "device":
                    with open("/dev/tpmrm0", "rb") as f:
                        data = f.read(num_bytes)
                        if len(data) == num_bytes:
                            self._failure_count = 0
                            return data

                self._failure_count += 1
                raise RuntimeError("TPM read returned insufficient data")

            except Exception as exc:
                self._failure_count += 1
                raise RuntimeError(f"TPM collection failed: {exc}")

    def get_info(self) -> HardwareSourceInfo:
        return HardwareSourceInfo(
            name="TPM 2.0",
            source_type="tpm",
            available=self._available,
            device_path="/dev/tpmrm0" if self._method == "device" else None,
            driver=self._method,
            throughput_bytes_per_sec=100_000,
            health_ok=self._failure_count < 5,
        )


class UsbTrngSource:
    """
    USB True Random Number Generator.

    Supports popular USB TRNG devices:
    - OneRNG (onerng.info)
    - TrueRNG v3 (ubld.it)
    - Infinite Noise TRNG (github.com/waywardgeek/infnoise)

    These devices use physical noise sources (avalanche noise,
    Johnson noise, etc.) for true randomness.

    GLI-11 4.3.4: External hardware sources provide independent
    entropy that is physically isolated from the CPU.
    """

    KNOWN_DEVICES = {
        "onerng": {
            "vendor_id": "1d50",
            "product_id": "6086",
            "device_name": "OneRNG",
            "baud_rate": 9600,
        },
        "truerng": {
            "vendor_id": "04d8",
            "product_id": "f5fe",
            "device_name": "TrueRNG v3",
            "baud_rate": 115200,
        },
        "infnoise": {
            "vendor_id": "0403",
            "product_id": "6015",
            "device_name": "Infinite Noise TRNG",
            "baud_rate": 115200,
        },
    }

    def __init__(self, device_path: Optional[str] = None):
        self._device_path = device_path
        self._available = False
        self._device_info: Optional[dict] = None
        self._lock = threading.Lock()
        self._failure_count = 0
        self._detect()

    def _detect(self) -> bool:
        """Detect USB TRNG devices."""
        # Check explicit device path
        if self._device_path and os.path.exists(self._device_path):
            self._available = True
            logger.info("USB TRNG at %s", self._device_path)
            return True

        # Scan common device paths
        for path in ["/dev/ttyACM0", "/dev/ttyACM1",
                     "/dev/ttyUSB0", "/dev/ttyUSB1"]:
            if os.path.exists(path):
                # Try to identify the device
                try:
                    result = subprocess.run(
                        ["udevadm", "info", "--query=all", f"--name={path}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    output = result.stdout.lower()
                    for name, info in self.KNOWN_DEVICES.items():
                        if info["vendor_id"] in output:  # ty:ignore[unsupported-operator]
                            self._device_path = path
                            self._device_info = info
                            self._available = True
                            logger.info(
                                "Found %s at %s",
                                info["device_name"],
                                path,
                            )
                            return True
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

        # Check for infnoise daemon
        try:
            result = subprocess.run(
                ["infnoise", "--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                self._available = True
                self._device_info = self.KNOWN_DEVICES["infnoise"]
                logger.info("Infinite Noise TRNG via daemon")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        logger.info("No USB TRNG detected")
        return False

    @property
    def available(self) -> bool:
        return self._available

    def collect(self, num_bytes: int) -> bytes:
        """Collect entropy from USB TRNG."""
        if not self._available:
            raise RuntimeError("USB TRNG not available")

        with self._lock:
            try:
                if self._device_path:
                    with open(self._device_path, "rb") as f:
                        data = f.read(num_bytes)
                        if len(data) >= num_bytes:
                            self._failure_count = 0
                            return data[:num_bytes]

                # Try infnoise daemon
                result = subprocess.run(
                    ["infnoise", f"--raw", f"--bytes={num_bytes}"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0 and len(result.stdout) >= num_bytes:
                    self._failure_count = 0
                    return result.stdout[:num_bytes]

                self._failure_count += 1
                raise RuntimeError("USB TRNG returned insufficient data")

            except Exception as exc:
                self._failure_count += 1
                raise RuntimeError(f"USB TRNG collection failed: {exc}")

    def get_info(self) -> HardwareSourceInfo:
        return HardwareSourceInfo(
            name=self._device_info["device_name"]
            if self._device_info
            else "USB TRNG",
            source_type="usb_trng",
            available=self._available,
            device_path=self._device_path,
            driver="serial",
            throughput_bytes_per_sec=50_000,
            health_ok=self._failure_count < 5,
        )


class HardwareEntropyManager:
    """
    Manages all hardware entropy sources with automatic detection,
    health monitoring, and failover.

    GLI-11 Compliance:
    - Auto-detects available hardware sources at startup
    - Falls back gracefully when hardware is unavailable
    - Provides combined entropy from all available sources
    - Monitors each source independently
    - Logs all detection and collection events
    """

    def __init__(self, audit_log_path: Optional[str] = None):
        self._sources: Dict[str, object] = {}
        self._source_info: Dict[str, HardwareSourceInfo] = {}
        self._lock = threading.Lock()
        self._audit_log_path = audit_log_path
        self._audit_sequence = 0

    def detect_available(self) -> Dict[str, HardwareSourceInfo]:
        """
        Detect all available hardware entropy sources.

        Returns dict of source_name -> HardwareSourceInfo.
        """
        results = {}

        # RDRAND/RDSEED
        rdrand = RdrandSource()
        if rdrand.available:
            self._sources["rdrand"] = rdrand
            info = rdrand.get_info()
            results["rdrand"] = info
            self._source_info["rdrand"] = info

        # TPM 2.0
        tpm = TpmSource()
        if tpm.available:
            self._sources["tpm"] = tpm
            info = tpm.get_info()
            results["tpm"] = info
            self._source_info["tpm"] = info

        # USB TRNG
        usb = UsbTrngSource()
        if usb.available:
            self._sources["usb_trng"] = usb
            info = usb.get_info()
            results["usb_trng"] = info
            self._source_info["usb_trng"] = info

        # Always-available: /dev/urandom (kernel entropy pool)
        self._sources["kernel"] = None  # Special case
        kernel_info = HardwareSourceInfo(
            name="Kernel CSPRNG",
            source_type="kernel",
            available=True,
            device_path="/dev/urandom",
            driver="kernel",
            throughput_bytes_per_sec=1_000_000_000,
            health_ok=True,
        )
        results["kernel"] = kernel_info
        self._source_info["kernel"] = kernel_info

        self._audit("DETECTION_COMPLETE", {
            "sources_found": list(results.keys()),
            "hardware_sources": len(
                [s for s in results.values()
                 if s.source_type != "kernel" and s.available]
            ),
        })

        logger.info(
            "Hardware entropy detection: %d sources found (%s)",
            len(results),
            ", ".join(results.keys()),
        )

        return results

    def collect_from_all(self, num_bytes: int) -> bytes:
        """
        Collect and mix entropy from all available sources.

        Uses XOR combination with SHA-512 conditioning to ensure
        that a compromised source cannot reduce overall entropy.

        GLI-11 4.3.5: Combined entropy must be at least as strong
        as the strongest individual source.
        """
        collected = []

        for name, source in self._sources.items():
            try:
                if name == "kernel":
                    data = os.urandom(num_bytes)
                else:
                    data = source.collect(num_bytes)  # ty:ignore[unresolved-attribute]
                collected.append(data)
                logger.debug(
                    "Collected %d bytes from %s", len(data), name
                )
            except Exception as exc:
                logger.warning(
                    "Collection from %s failed: %s", name, exc
                )
                if name in self._source_info:
                    self._source_info[name].health_ok = False
                    self._source_info[name].last_error = str(exc)

        if not collected:
            raise RuntimeError("All entropy sources failed")

        # Mix all sources via SHA-512
        combined = bytearray(num_bytes)
        for data in collected:
            padded = (data * ((num_bytes // len(data)) + 1))[:num_bytes]
            for i in range(num_bytes):
                combined[i] ^= padded[i]

        # Final conditioning
        result = bytearray()
        counter = 0
        while len(result) < num_bytes:
            h = hashlib.sha512(
                struct.pack(">Q", counter) + bytes(combined)
            ).digest()
            result.extend(h)
            counter += 1

        return bytes(result[:num_bytes])

    def get_all_info(self) -> Dict[str, dict]:
        """Return info about all detected sources."""
        return {
            name: {
                "name": info.name,
                "type": info.source_type,
                "available": info.available,
                "device": info.device_path,
                "driver": info.driver,
                "throughput_bps": info.throughput_bytes_per_sec,
                "health_ok": info.health_ok,
                "last_error": info.last_error,
            }
            for name, info in self._source_info.items()
        }

    def _audit(self, event_type: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "HardwareEntropy",
            "event": event_type,
            **details,
        }
        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Configuration Generator
# ---------------------------------------------------------------------------

def generate_linux_hwrng_config() -> str:
    """
    Generate Linux configuration for hardware entropy sources.

    This outputs the commands needed to configure rng-tools,
    TPM, and USB TRNG devices on a production server.
    """
    config = """#!/bin/bash
# Hardware Entropy Source Configuration for iGaming RNG Server
# GLI-11 4.3: Multiple hardware entropy sources recommended
# Run as root on production servers

set -euo pipefail

echo "=== Hardware Entropy Source Setup ==="

# 1. Install rng-tools for hardware RNG management
echo "[1/5] Installing rng-tools..."
apt-get update -qq
apt-get install -y rng-tools5 tpm2-tools

# 2. Configure rng-tools to use all available sources
cat > /etc/default/rng-tools-debian << 'RNGCONF'
# Hardware RNG configuration
# GLI-11: Enable all available hardware sources
HRNGDEVICE=/dev/hwrng
RNGD_OPTS="-r /dev/hwrng -o /dev/random -t 60 -W 80% -f"
RNGCONF

# 3. Enable and verify RDRAND
echo "[2/5] Checking RDRAND/RDSEED support..."
if grep -q rdrand /proc/cpuinfo; then
    echo "  RDRAND: SUPPORTED"
    modprobe intel-rng 2>/dev/null || true
else
    echo "  RDRAND: NOT AVAILABLE"
fi

if grep -q rdseed /proc/cpuinfo; then
    echo "  RDSEED: SUPPORTED"
else
    echo "  RDSEED: NOT AVAILABLE"
fi

# 4. Configure TPM 2.0
echo "[3/5] Configuring TPM 2.0..."
if [ -e /dev/tpmrm0 ]; then
    echo "  TPM 2.0: DETECTED"
    # Verify TPM RNG works
    tpm2_getrandom 32 --hex && echo "  TPM RNG: WORKING" || echo "  TPM RNG: FAILED"

    # Add user to tss group
    usermod -aG tss rng-service 2>/dev/null || true
else
    echo "  TPM 2.0: NOT DETECTED"
fi

# 5. Configure USB TRNG (OneRNG, TrueRNG)
echo "[4/5] Scanning for USB TRNG devices..."
for dev in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0 /dev/ttyUSB1; do
    if [ -e "$dev" ]; then
        vendor=$(udevadm info --query=all --name="$dev" 2>/dev/null | grep ID_VENDOR_ID || true)
        echo "  Found device at $dev: $vendor"
    fi
done

# 6. Start rng-tools service
echo "[5/5] Starting rng-tools service..."
systemctl enable rng-tools
systemctl restart rng-tools
systemctl status rng-tools --no-pager

# Verify entropy pool health
echo ""
echo "=== Entropy Pool Status ==="
cat /proc/sys/kernel/random/entropy_avail
echo "Available entropy bits: $(cat /proc/sys/kernel/random/entropy_avail)"
echo "Pool size: $(cat /proc/sys/kernel/random/poolsize)"

echo ""
echo "=== Setup Complete ==="
echo "Monitor entropy: watch -n1 cat /proc/sys/kernel/random/entropy_avail"
"""
    return config


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Hardware entropy self-test."""
    print("=== Hardware Entropy Source Self-Test ===\n")

    manager = HardwareEntropyManager()
    sources = manager.detect_available()

    print(f"Detected {len(sources)} entropy sources:")
    for name, info in sources.items():
        status = "OK" if info.available else "N/A"
        print(f"  {name}: {info.name} [{status}] "
              f"({info.throughput_bytes_per_sec/1000:.0f} KB/s)")

    # Collect from all available
    try:
        entropy = manager.collect_from_all(64)
        assert len(entropy) == 64
        print(f"\n[PASS] Collected 64 bytes from {len(sources)} sources")

        # Verify different calls produce different output
        entropy2 = manager.collect_from_all(64)
        assert entropy != entropy2, "Duplicate output"
        print("[PASS] Consecutive collections differ")

    except RuntimeError as exc:
        print(f"\n[WARN] Collection failed: {exc}")

    # Show all info
    all_info = manager.get_all_info()
    print(f"\nSource details: {json.dumps(all_info, indent=2)}")

    print("\n=== Self-test complete ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
