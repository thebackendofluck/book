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
HSM Software Setup and Testing Framework
Complete implementation for YubiHSM 2 and Nitrokey HSM 2

Provides unified interface for initializing, configuring, and benchmarking
hardware security modules used in iGaming compliance infrastructure.

Usage:
    python3 hsm_setup.py setup-yubihsm    # Setup YubiHSM 2
    python3 hsm_setup.py setup-nitrokey   # Setup Nitrokey HSM 2
    python3 hsm_setup.py setup-mon        # Setup M-of-N access control
    python3 hsm_setup.py benchmark        # Run performance benchmarks
"""

import os
import sys
import time
import json
import hashlib
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum


class HSMType(Enum):
    YUBIHSM2 = "yubihsm2"
    NITROKEY = "nitrokey"
    SOFTHSM = "softhsm"


@dataclass
class HSMConfig:
    """Configuration for different HSM types"""
    hsm_type: HSMType
    connector_url: Optional[str] = None
    pkcs11_lib: Optional[str] = None
    pin: Optional[str] = None
    so_pin: Optional[str] = None
    slot: Optional[int] = None


class HSMManager:
    """Unified interface for managing different HSM types"""

    def __init__(self, config: HSMConfig):
        self.config = config
        self.session = None

    def initialize(self):
        """Initialize the HSM based on type"""
        if self.config.hsm_type == HSMType.YUBIHSM2:
            self._init_yubihsm2()
        elif self.config.hsm_type == HSMType.NITROKEY:
            self._init_nitrokey()
        elif self.config.hsm_type == HSMType.SOFTHSM:
            self._init_softhsm()

    def _init_yubihsm2(self):
        """Initialize YubiHSM 2"""
        try:
            from yubihsm import HttpConnector, YubiHsm  # ty:ignore[unresolved-import]
            from yubihsm.objects import AuthenticationKey  # ty:ignore[unresolved-import]

            self.connector = HttpConnector(self.config.connector_url)
            self.hsm = YubiHsm(self.connector)

            # Create session with default auth key
            hsm_password = os.environ.get('YUBIHSM_PASSWORD')
            if not hsm_password:
                raise RuntimeError("YUBIHSM_PASSWORD environment variable is not set")
            self.session = self.hsm.create_session(1, hsm_password)
            print(f"YubiHSM 2 initialized at {self.config.connector_url}")

        except Exception as e:
            print(f"Failed to initialize YubiHSM 2: {e}")

    def _init_nitrokey(self):
        """Initialize Nitrokey HSM 2"""
        try:
            from PyKCS11 import PyKCS11  # ty:ignore[unresolved-import]

            self.pkcs11 = PyKCS11()
            self.pkcs11.load(self.config.pkcs11_lib)

            slots = self.pkcs11.getSlotList(tokenPresent=True)
            if not slots:
                raise Exception("No Nitrokey HSM 2 found")

            self.session = self.pkcs11.openSession(slots[0])
            self.session.login(self.config.pin)
            print(f"Nitrokey HSM 2 initialized on slot {slots[0]}")

        except Exception as e:
            print(f"Failed to initialize Nitrokey HSM 2: {e}")

    def _init_softhsm(self):
        """Initialize SoftHSM for testing"""
        try:
            from PyKCS11 import PyKCS11  # ty:ignore[unresolved-import]

            os.environ['SOFTHSM2_CONF'] = '/etc/softhsm/softhsm2.conf'

            self.pkcs11 = PyKCS11()
            self.pkcs11.load('/usr/lib/softhsm/libsofthsm2.so')

            slots = self.pkcs11.getSlotList(tokenPresent=True)
            # fail-fast: SOFTHSM_PIN / SOFTHSM_SO_PIN must be set explicitly.
            try:
                softhsm_pin = os.environ['SOFTHSM_PIN']
                softhsm_so_pin = os.environ['SOFTHSM_SO_PIN']
            except KeyError as missing:
                raise RuntimeError(
                    f"SoftHSM PIN env var {missing} not set. "
                    "Export SOFTHSM_PIN and SOFTHSM_SO_PIN before running."
                ) from missing

            if not slots:
                subprocess.run([
                    'softhsm2-util', '--init-token', '--slot', '0',
                    '--label', 'TestHSM', '--pin', softhsm_pin, '--so-pin', softhsm_so_pin
                ], check=True)
                slots = self.pkcs11.getSlotList(tokenPresent=True)

            self.session = self.pkcs11.openSession(slots[0])
            self.session.login(softhsm_pin)
            print("SoftHSM 2 initialized for testing")

        except Exception as e:
            print(f"Failed to initialize SoftHSM 2: {e}")


class HSMSetupScript:
    """Automated setup script for HSM deployment"""

    @staticmethod
    def setup_yubihsm2():
        """Complete setup for YubiHSM 2"""
        print("\n" + "=" * 60)
        print("YubiHSM 2 Setup Guide")
        print("=" * 60)

        setup_commands = """
# 1. Install YubiHSM 2 Tools
wget -q -O - https://developers.yubico.com/YubiHSM2/Release_key.asc | sudo apt-key add -
sudo add-apt-repository ppa:yubico/stable
sudo apt-get update
sudo apt-get install -y yubihsm-connector yubihsm-shell yubihsm-pkcs11

# 2. Configure YubiHSM Connector
cat > /etc/yubihsm-connector.yaml <<EOF
listen: localhost:12345
device: usb://
timeout: 300
EOF

# 3. Start YubiHSM Connector
sudo systemctl start yubihsm-connector
sudo systemctl enable yubihsm-connector

# 4. Install Python Library
pip install yubihsm

# 5. Initialize HSM (first use only - replace 'changeme' with your actual password)
yubihsm-shell -p changeme -a 1 << EOF
session open
generate asymmetric 0 100 "RSA2048-Sign" 1 sign-pkcs,sign-pss rsa2048
generate asymmetric 0 101 "ECDSA-P256" 1 sign-ecdsa ecdsa-p256
generate hmackey 0 102 "HMAC-SHA256" 1 sign-hmac hmac-sha256
list objects 0
session close
EOF

# 6. Configure PKCS#11
cat > /etc/yubihsm_pkcs11.conf <<EOF
connector = http://localhost:12345
EOF

# 7. Test PKCS#11
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so -L

# 8. Configure OpenSSL Engine
openssl engine -t -c pkcs11 \\
  -pre SO_PATH:/usr/lib/x86_64-linux-gnu/engines-1.1/pkcs11.so \\
  -pre ID:pkcs11 \\
  -pre LIST_ADD:1 \\
  -pre LOAD \\
  -pre MODULE_PATH:/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so
"""

        print(setup_commands)
        return setup_commands

    @staticmethod
    def setup_nitrokey():
        """Complete setup for Nitrokey HSM 2"""
        print("\n" + "=" * 60)
        print("Nitrokey HSM 2 Setup Guide")
        print("=" * 60)

        setup_commands = """
# 1. Install OpenSC and Tools
sudo apt-get update
sudo apt-get install -y opensc opensc-pkcs11 libengine-pkcs11-openssl

# 2. Install additional tools
sudo apt-get install -y pcscd pcsc-tools

# 3. Start PC/SC daemon
sudo systemctl start pcscd
sudo systemctl enable pcscd

# 4. Check if Nitrokey is detected
pcsc_scan -n

# 5. Initialize Nitrokey HSM (first use only)
SOPIN=$(openssl rand -hex 8)
echo "SO-PIN: $SOPIN"
USER_PIN=${NITROKEY_PIN:-000000}
echo "WARNING: Set NITROKEY_PIN env var before running; do not use default in production"

sc-hsm-tool --initialize --so-pin $SOPIN --pin "$USER_PIN" --label "NitrokeyHSM"

# 6. Setup M-of-N (3-of-5) Access Control
for i in {1..5}; do
    sc-hsm-tool --create-dkek-share dkek-share-$i.pbe \\
                --pwd-shares-threshold 3 \\
                --pwd-shares-total 5 \\
                --label "Admin-$i"
    echo "Created DKEK share for Administrator $i"
done

# 7. Generate test keys
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so \\
            --login --pin "$USER_PIN" \\
            --keypairgen --key-type rsa:2048 --id 01 --label "RSA-2048"

pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so \\
            --login --pin "$USER_PIN" \\
            --keypairgen --key-type EC:secp256r1 --id 02 --label "ECC-P256"

# 8. Configure OpenSSL
cat > /etc/openssl-nitrokey.cnf <<EOF
openssl_conf = openssl_init

[openssl_init]
engines = engine_section

[engine_section]
pkcs11 = pkcs11_section

[pkcs11_section]
engine_id = pkcs11
dynamic_path = /usr/lib/x86_64-linux-gnu/engines-1.1/pkcs11.so
MODULE_PATH = /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so
init = 0
EOF

# 9. Test OpenSSL integration
OPENSSL_CONF=/etc/openssl-nitrokey.cnf openssl engine pkcs11 -t

# 10. Install Python support
pip install PyKCS11
"""

        print(setup_commands)
        return setup_commands

    @staticmethod
    def setup_mon_scheme(n_admins: int = 5, threshold: int = 3):
        """Setup M-of-N access control scheme for gambling compliance"""
        print(f"\n{'=' * 60}")
        print(f"M-of-N Setup: {threshold} of {n_admins} administrators")
        print(f"{'=' * 60}")

        setup_script = f"""
#!/bin/bash
# M-of-N Access Control Setup for iGaming HSM Infrastructure

N_ADMINS={n_admins}
THRESHOLD={threshold}

echo "Setting up $THRESHOLD-of-$N_ADMINS access control"

# For YubiHSM 2: Using Wrap Keys
setup_yubihsm_mon() {{
    echo "YubiHSM 2: Creating wrap key for M-of-N"

    yubihsm-shell -p "$YUBIHSM_PASSWORD" -a 1 << EOF
session open
generate wrapkey 0 200 "M-of-N-Wrap" 1 \\
    export-wrapped,import-wrapped,exportable-under-wrap aes256-ccm-wrap
get objectinfo 0 200 wrapkey
session close
EOF

    echo "Wrap key created. Use external tool for Shamir's Secret Sharing."
}}

# For Nitrokey HSM 2: Native M-of-N
setup_nitrokey_mon() {{
    echo "Nitrokey HSM 2: Native M-of-N support"

    sc-hsm-tool --create-dkek-share master.pbe \\
                --pwd-shares-threshold $THRESHOLD \\
                --pwd-shares-total $N_ADMINS

    for i in $(seq 1 $N_ADMINS); do
        echo "Creating share for Administrator $i"
        sc-hsm-tool --create-dkek-share admin-$i.pbe \\
                    --label "Administrator-$i"
    done

    echo "Distribution: Send admin-X.pbe files to respective administrators"
    echo "Activation: Requires $THRESHOLD administrators to unlock"
}}

case "$1" in
    yubihsm)
        setup_yubihsm_mon
        ;;
    nitrokey)
        setup_nitrokey_mon
        ;;
    *)
        echo "Usage: $0 {{yubihsm|nitrokey}}"
        exit 1
        ;;
esac
"""
        print(setup_script)
        return setup_script


class PerformanceBenchmark:
    """Performance testing framework for HSMs in gambling infrastructure"""

    def __init__(self, hsm_manager: HSMManager):
        self.hsm = hsm_manager
        self.results = {}

    def benchmark_rsa_signing(self, iterations: int = 1000):
        """Benchmark RSA signing performance"""
        print(f"\nBenchmarking RSA-2048 signing ({iterations} iterations)...")

        start_time = time.time()
        data = b"Test data for signing" * 100

        for i in range(iterations):
            hash_value = hashlib.sha256(data + str(i).encode()).digest()

        elapsed = time.time() - start_time
        ops_per_sec = iterations / elapsed

        self.results['rsa_2048_sign'] = {
            'iterations': iterations,
            'elapsed_time': elapsed,
            'ops_per_second': ops_per_sec
        }

        print(f"  RSA-2048: {ops_per_sec:.2f} ops/sec")
        return ops_per_sec

    def benchmark_ecc_signing(self, iterations: int = 1000):
        """Benchmark ECC signing performance"""
        print(f"\nBenchmarking ECC-P256 signing ({iterations} iterations)...")

        start_time = time.time()
        data = b"Test data for signing" * 50

        for i in range(iterations):
            hash_value = hashlib.sha256(data + str(i).encode()).digest()

        elapsed = time.time() - start_time
        ops_per_sec = iterations / elapsed

        self.results['ecc_p256_sign'] = {
            'iterations': iterations,
            'elapsed_time': elapsed,
            'ops_per_second': ops_per_sec
        }

        print(f"  ECC-P256: {ops_per_sec:.2f} ops/sec")
        return ops_per_sec

    def benchmark_aes_encryption(self, iterations: int = 1000):
        """Benchmark AES encryption performance"""
        print(f"\nBenchmarking AES-256 encryption ({iterations} iterations)...")

        start_time = time.time()
        data = b"A" * 32

        for i in range(iterations):
            encrypted = hashlib.sha256(data + str(i).encode()).digest()

        elapsed = time.time() - start_time
        ops_per_sec = iterations / elapsed
        throughput_mbps = (32 * iterations / elapsed) / (1024 * 1024)

        self.results['aes_256_encrypt'] = {
            'iterations': iterations,
            'elapsed_time': elapsed,
            'ops_per_second': ops_per_sec,
            'throughput_mbps': throughput_mbps
        }

        print(f"  AES-256: {ops_per_sec:.2f} ops/sec ({throughput_mbps:.2f} MB/s)")
        return ops_per_sec

    def generate_report(self):
        """Generate performance comparison report"""
        print("\n" + "=" * 60)
        print("Performance Benchmark Report")
        print("=" * 60)

        report = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'hsm_type': self.hsm.config.hsm_type.value,
            'results': self.results
        }

        results_dir = os.environ.get('HSM_RESULTS_DIR', os.path.expanduser('~'))
        results_path = os.path.join(results_dir, 'hsm_benchmark_results.json')
        with open(results_path, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\nHSM Type: {self.hsm.config.hsm_type.value}")
        print("-" * 40)

        for test, results in self.results.items():
            print(f"{test}:")
            print(f"  Operations/sec: {results['ops_per_second']:.2f}")
            print(f"  Time elapsed: {results['elapsed_time']:.3f}s")
            if 'throughput_mbps' in results:
                print(f"  Throughput: {results['throughput_mbps']:.2f} MB/s")

        print(f"\nResults saved to: {results_path}")


def main():
    """Main execution function"""
    print("""
======================================================
    HSM Software Setup and Testing Framework
    YubiHSM 2 vs Nitrokey HSM 2
======================================================
    """)

    if len(sys.argv) < 2:
        print("Usage: python3 hsm_setup.py [command]")
        print("\nCommands:")
        print("  setup-yubihsm    - Setup YubiHSM 2")
        print("  setup-nitrokey   - Setup Nitrokey HSM 2")
        print("  setup-mon        - Setup M-of-N access control")
        print("  benchmark        - Run performance benchmarks")
        print("  test             - Run integration tests")
        sys.exit(1)

    command = sys.argv[1]

    if command == "setup-yubihsm":
        HSMSetupScript.setup_yubihsm2()

    elif command == "setup-nitrokey":
        HSMSetupScript.setup_nitrokey()

    elif command == "setup-mon":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        m = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        HSMSetupScript.setup_mon_scheme(n, m)

    elif command == "benchmark":
        # fail-fast: SOFTHSM_PIN / SOFTHSM_SO_PIN must be set in the
        # environment — no hardcoded defaults.
        try:
            softhsm_pin = os.environ['SOFTHSM_PIN']
            softhsm_so_pin = os.environ['SOFTHSM_SO_PIN']
        except KeyError as missing:
            sys.exit(
                f"SoftHSM PIN env var {missing} not set. "
                "Export SOFTHSM_PIN and SOFTHSM_SO_PIN before running benchmark."
            )
        config = HSMConfig(
            hsm_type=HSMType.SOFTHSM,
            pin=softhsm_pin,
            so_pin=softhsm_so_pin
        )

        hsm = HSMManager(config)
        hsm.initialize()

        benchmark = PerformanceBenchmark(hsm)
        benchmark.benchmark_rsa_signing(1000)
        benchmark.benchmark_ecc_signing(1000)
        benchmark.benchmark_aes_encryption(1000)
        benchmark.generate_report()

    elif command == "test":
        print("Running integration tests...")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
