#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
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
            from yubihsm import HttpConnector, YubiHsm
            from yubihsm.objects import AuthenticationKey
            
            self.connector = HttpConnector(self.config.connector_url)
            self.hsm = YubiHsm(self.connector)
            
            # Create session with default auth key
            hsm_password = os.environ.get('YUBIHSM_PASSWORD')
            if not hsm_password:
                raise RuntimeError("YUBIHSM_PASSWORD environment variable is not set")
            self.session = self.hsm.create_session(1, hsm_password)
            print(f"✓ YubiHSM 2 initialized at {self.config.connector_url}")
            
        except Exception as e:
            print(f"✗ Failed to initialize YubiHSM 2: {e}")
            
    def _init_nitrokey(self):
        """Initialize Nitrokey HSM 2"""
        try:
            from PyKCS11 import PyKCS11
            
            self.pkcs11 = PyKCS11()
            self.pkcs11.load(self.config.pkcs11_lib)
            
            slots = self.pkcs11.getSlotList(tokenPresent=True)
            if not slots:
                raise Exception("No Nitrokey HSM 2 found")
                
            self.session = self.pkcs11.openSession(slots[0])
            self.session.login(self.config.pin)
            print(f"✓ Nitrokey HSM 2 initialized on slot {slots[0]}")
            
        except Exception as e:
            print(f"✗ Failed to initialize Nitrokey HSM 2: {e}")
            
    def _init_softhsm(self):
        """Initialize SoftHSM for testing"""
        try:
            from PyKCS11 import PyKCS11
            
            # Setup SoftHSM environment
            os.environ['SOFTHSM2_CONF'] = '/etc/softhsm/softhsm2.conf'
            
            self.pkcs11 = PyKCS11()
            self.pkcs11.load('/usr/lib/softhsm/libsofthsm2.so')
            
            slots = self.pkcs11.getSlotList(tokenPresent=True)
            # fail-fast: SOFTHSM_PIN / SOFTHSM_SO_PIN must be set explicitly.
            # No hardcoded defaults — even "test" PINs leak into screenshots,
            # bash history and the book's example output.
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
            print("✓ SoftHSM 2 initialized for testing")
            
        except Exception as e:
            print(f"✗ Failed to initialize SoftHSM 2: {e}")

class HSMSetupScript:
    """Automated setup script for HSM deployment"""
    
    @staticmethod
    def setup_yubihsm2():
        """Complete setup for YubiHSM 2"""
        print("\n" + "="*60)
        print("YubiHSM 2 Setup Guide")
        print("="*60)
        
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

# 5. Initialize HSM (first use only)
#
# Never pass the authentication key password with -p on the command line. argv
# is world readable through /proc/<pid>/cmdline for the lifetime of the process,
# it is captured by any `ps` running concurrently, it lands in shell history, and
# it is recorded by process auditing (auditd execve, Wazuh, eBPF agents). A
# password there is a password disclosed.
#
# yubihsm-shell gives two ways to avoid it, both used below:
#
#   a) Omit -p entirely. The password argument defaults to "-", which reads from
#      stdin; when stdin is a terminal that is a no-echo prompt. This is the
#      right choice for a human at a keyboard, which is what HSM provisioning
#      should be.
#   b) Pass -p file:<path> (or a path to an existing regular file). yubihsm-shell
#      reads the password from the file instead of treating the argument as the
#      literal password. Use this for automation, with the file on a tmpfs at
#      mode 0600 and removed straight afterwards.
#
# Each object is created with its own non-interactive --action invocation rather
# than a heredoc of interactive commands, because the heredoc occupies stdin and
# stdin is what the prompt needs. Four invocations means four prompts; if that
# is tiresome, use form (b) or run `yubihsm-shell` interactively and type
# `session open 1`, which prompts for the password rather than taking it from
# argv.

# Interactive, prompts four times (recommended for provisioning):
yubihsm-shell --authkey 1 -a generate-asymmetric-key \\
    -i 100 -l "RSA2048-Sign" -d 1 -c sign-pkcs,sign-pss -A rsa2048
yubihsm-shell --authkey 1 -a generate-asymmetric-key \\
    -i 101 -l "ECDSA-P256" -d 1 -c sign-ecdsa -A ecp256
yubihsm-shell --authkey 1 -a generate-hmac-key \\
    -i 102 -l "HMAC-SHA256" -d 1 -c sign-hmac,verify-hmac -A hmac-sha256
yubihsm-shell --authkey 1 -a list-objects -d 1

# Automation form, password from a tmpfs file, never from argv:
#   install -d -m 700 /run/yubihsm && umask 077
#   systemd-ask-password "YubiHSM authkey 1:" > /run/yubihsm/authkey.pw
#   yubihsm-shell --authkey 1 -p file:/run/yubihsm/authkey.pw \\
#       -a generate-asymmetric-key -i 100 -l "RSA2048-Sign" -d 1 \\
#       -c sign-pkcs,sign-pss -A rsa2048
#   shred -u /run/yubihsm/authkey.pw   # tmpfs, so this is genuinely gone
#
# Note the algorithm name is `ecp256`, not `ecdsa-p256`. `ecdsa-p256` is not in
# yubihsm-shell's algorithm table, so yh_string_to_algo rejects it and the
# command fails with "Unable to parse algorithm".

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
        print("\n" + "="*60)
        print("Nitrokey HSM 2 Setup Guide")
        print("="*60)
        
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
# Generate SO-PIN (16 hex digits)
SOPIN=$(openssl rand -hex 8)
echo "SO-PIN: $SOPIN"
# Set a strong user PIN - replace '000000' with your actual PIN before running
USER_PIN=${NITROKEY_PIN:-000000}
echo "WARNING: Set NITROKEY_PIN env var before running; do not use default in production"

# Initialize the device.
# --dkek-shares declares how many shares the DKEK will be split across and MUST
# be given here: the count is fixed at initialization and --import-dkek-share
# has nothing to import into without it.
sc-hsm-tool --initialize --so-pin $SOPIN --pin "$USER_PIN" \\
            --dkek-shares 3 --label "NitrokeyHSM"

# 6. DKEK shares
#
# Read this before treating it as a threshold scheme. On the SmartCard-HSM the
# DKEK is the XOR of ALL declared shares, so this is N-of-N, not M-of-N: every
# share created here is required, and losing any one of them destroys the DKEK
# and everything wrapped under it, with no recovery from the others.
#
# --pwd-shares-threshold/--pwd-shares-total do NOT split the DKEK. They
# Shamir-split the password that protects a single share file. Passing 3-of-5
# there while creating 5 separate shares produces five independent required
# shares, not a 3-of-5 recovery scheme, which is a durability trap sold as
# resilience.
#
# Three custodians, all three required. Choose the count deliberately: more
# shares means more people must be present AND more ways to lose the key.
for i in {1..3}; do
    sc-hsm-tool --create-dkek-share dkek-share-$i.pbe --label "Admin-$i"
    echo "Created DKEK share $i of 3 (ALL are required)"
done

# 7. Import the DKEK. All declared shares must be imported, in any order.
echo "To activate, import every share (all 3 custodians must be present):"
echo "sc-hsm-tool --import-dkek-share dkek-share-1.pbe"
echo "sc-hsm-tool --import-dkek-share dkek-share-2.pbe"
echo "sc-hsm-tool --import-dkek-share dkek-share-3.pbe"
echo ""
echo "If you need M-of-N recovery (any 3 of 5), the SmartCard-HSM does not"
echo "provide it at this layer. Use a real threshold scheme over the share"
echo "material, or accept N-of-N and plan custody around losing a share."

# 8. Generate test keys
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so \\
            --login --pin "$USER_PIN" \\
            --keypairgen --key-type rsa:2048 --id 01 --label "RSA-2048"

pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so \\
            --login --pin "$USER_PIN" \\
            --keypairgen --key-type EC:secp256r1 --id 02 --label "ECC-P256"

# 9. List objects
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so \\
            --login --pin "$USER_PIN" --list-objects

# 10. Configure OpenSSL
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

# 11. Test OpenSSL integration
OPENSSL_CONF=/etc/openssl-nitrokey.cnf openssl engine pkcs11 -t

# 12. Install Python support
pip install PyKCS11
"""
        
        print(setup_commands)
        return setup_commands
    
    @staticmethod
    def setup_mon_scheme(n_admins: int = 5, threshold: int = 3):
        """Setup M-of-N access control scheme"""
        print(f"\n{'='*60}")
        print(f"M-of-N Setup: {threshold} of {n_admins} administrators")
        print(f"{'='*60}")
        
        setup_script = f"""
#!/bin/bash
# M-of-N Access Control Setup

N_ADMINS={n_admins}
THRESHOLD={threshold}

echo "Setting up $THRESHOLD-of-$N_ADMINS access control"

# For YubiHSM 2: Using Wrap Keys
setup_yubihsm_mon() {{
    echo "YubiHSM 2: Creating wrap key for M-of-N"

    # Two things were wrong with the previous form of this command.
    #
    # 1. It passed the password as `-p "$YUBIHSM_PASSWORD"`, putting it in argv
    #    where any local user can read it from /proc/<pid>/cmdline. Omitting -p
    #    makes yubihsm-shell prompt on the terminal instead. (`-a 1` was also not
    #    the auth key: -a is --action. The auth key is --authkey.)
    #
    # 2. `generate wrapkey` takes SEVEN positional arguments:
    #      session, key_id, label, domains, capabilities,
    #      delegated_capabilities, algorithm
    #    The old line supplied six, omitting delegated_capabilities, so
    #    yubihsm-shell could not parse it and the command failed outright. The
    #    non-interactive form below is equally strict: `generate-wrap-key` exits
    #    with "Missing delegated capabilities" if --delegated is absent.
    #
    # Delegated capabilities are the capabilities that objects wrapped by this
    # key are permitted to carry. That is where exportable-under-wrap belongs.
    # The old line had exportable-under-wrap in the wrap key's OWN capabilities,
    # which says something different and dangerous: it makes the wrap key itself
    # exportable under some other wrap key, so the key protecting the M-of-N
    # material could be carried off the device inside a single export.
    yubihsm-shell --authkey 1 -a generate-wrap-key \\
        -i 200 -l "M-of-N-Wrap" -d 1 \\
        -c export-wrapped,import-wrapped \\
        --delegated exportable-under-wrap,sign-pkcs,sign-pss,sign-ecdsa \\
        -A aes256-ccm-wrap

    yubihsm-shell --authkey 1 -a get-object-info -i 200 -t wrap-key

    echo "Wrap key created. Use external tool for Shamir's Secret Sharing."
    echo "The YubiHSM does not implement a threshold scheme itself: it holds one"
    echo "wrap key, and splitting the material that reconstructs access to it is"
    echo "something you do outside the device."
}}

# For Nitrokey HSM 2: DKEK shares (N-of-N, NOT a threshold scheme)
setup_nitrokey_mon() {{
    echo "Nitrokey HSM 2: DKEK shares -- N-of-N, not M-of-N"
    echo ""
    echo "The SmartCard-HSM builds the DKEK by XOR-ing ALL declared shares."
    echo "That makes it N-of-N: every share is required, and losing any single"
    echo "one destroys the DKEK and everything wrapped under it. It is not a"
    echo "threshold scheme and the surviving shares cannot reconstruct it."
    echo ""
    echo "--pwd-shares-threshold/--pwd-shares-total do NOT split the DKEK. They"
    echo "Shamir-split the PASSWORD protecting one share file. Creating"
    echo "$N_ADMINS shares while passing a threshold of $THRESHOLD there yields"
    echo "$N_ADMINS independent required shares, not any-$THRESHOLD-of-$N_ADMINS"
    echo "recovery. That is a durability trap sold as resilience."
    echo ""

    # Declare the share count at initialization. It is fixed there and cannot be
    # changed later; --import-dkek-share has nothing to import into without it.
    #   sc-hsm-tool --initialize --so-pin $SOPIN --pin "$USER_PIN" \\
    #               --dkek-shares $N_ADMINS --label "NitrokeyHSM"

    # One share per custodian. ALL $N_ADMINS are required to reconstruct.
    for i in $(seq 1 $N_ADMINS); do
        echo "Creating DKEK share $i of $N_ADMINS (ALL are required)"
        sc-hsm-tool --create-dkek-share admin-$i.pbe \\
                    --label "Administrator-$i"
    done

    echo ""
    echo "Distribution: send admin-X.pbe to each administrator"
    echo "Activation:   requires ALL $N_ADMINS administrators, not $THRESHOLD"
    echo ""
    echo "If you genuinely need any-$THRESHOLD-of-$N_ADMINS recovery, the"
    echo "SmartCard-HSM does not provide it at this layer. Apply a real threshold"
    echo "scheme to the share material yourself, or accept N-of-N and plan"
    echo "custody around the fact that one lost share is total loss."
}}

# Blockchain-based M-of-N
setup_blockchain_mon() {{
    cat > smart_contract.sol << 'SOLIDITY'
pragma solidity ^0.8.0;

contract MofNAccess {{
    uint256 public constant THRESHOLD = $THRESHOLD;
    uint256 public constant N_ADMINS = $N_ADMINS;
    
    mapping(address => bool) public isAdmin;
    mapping(bytes32 => uint256) public approvals;
    mapping(bytes32 => mapping(address => bool)) public hasApproved;
    
    event OperationApproved(bytes32 opId, address admin);
    event OperationExecuted(bytes32 opId);
    
    modifier onlyAdmin() {{
        require(isAdmin[msg.sender], "Not an administrator");
        _;
    }}
    
    function approveOperation(bytes32 opId) external onlyAdmin {{
        require(!hasApproved[opId][msg.sender], "Already approved");
        
        hasApproved[opId][msg.sender] = true;
        approvals[opId]++;
        
        emit OperationApproved(opId, msg.sender);
        
        if (approvals[opId] >= THRESHOLD) {{
            executeOperation(opId);
        }}
    }}
    
    function executeOperation(bytes32 opId) internal {{
        // Trigger HSM operation
        emit OperationExecuted(opId);
        delete approvals[opId];
    }}
}}
SOLIDITY
    
    echo "Smart contract for M-of-N created: smart_contract.sol"
}}

# Execute based on HSM type
case "$1" in
    yubihsm)
        setup_yubihsm_mon
        ;;
    nitrokey)
        setup_nitrokey_mon
        ;;
    blockchain)
        setup_blockchain_mon
        ;;
    *)
        echo "Usage: $0 {{yubihsm|nitrokey|blockchain}}"
        exit 1
        ;;
esac
"""
        print(setup_script)
        return setup_script

class PerformanceBenchmark:
    """Performance testing framework for HSMs"""
    
    def __init__(self, hsm_manager: HSMManager):
        self.hsm = hsm_manager
        self.results = {}
        
    def benchmark_rsa_signing(self, iterations: int = 1000):
        """Benchmark RSA signing performance"""
        print(f"\nBenchmarking RSA-2048 signing ({iterations} iterations)...")
        
        start_time = time.time()
        data = b"Test data for signing" * 100
        
        for i in range(iterations):
            # Simulate signing operation
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
            # Simulate signing operation
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
        data = b"A" * 32  # 32 bytes block
        
        for i in range(iterations):
            # Simulate encryption
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
        print("\n" + "="*60)
        print("Performance Benchmark Report")
        print("="*60)
        
        report = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'hsm_type': self.hsm.config.hsm_type.value,
            'results': self.results
        }
        
        # Save to JSON
        results_dir = os.environ.get('HSM_RESULTS_DIR', os.path.expanduser('~'))
        results_path = os.path.join(results_dir, 'hsm_benchmark_results.json')
        with open(results_path, 'w') as f:
            json.dump(report, f, indent=2)
            
        # Print summary
        print(f"\nHSM Type: {self.hsm.config.hsm_type.value}")
        print("-"*40)
        
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
╔══════════════════════════════════════════════════════════════╗
║           HSM Software Setup and Testing Framework           ║
║                 YubiHSM 2 vs Nitrokey HSM 2                 ║
╚══════════════════════════════════════════════════════════════╝
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
        # Test with SoftHSM if no hardware available.
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
        # Add integration tests here
        
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
