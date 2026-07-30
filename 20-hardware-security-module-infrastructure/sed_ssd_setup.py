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

# YubiHSM 2 SED SSD Automated Setup Script
# Automated configuration and management of Self-Encrypting Drives with YubiHSM integration

import sys
import os
import json
import subprocess
import argparse
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from yubihsm import YubiHsm
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
    from yubihsm.objects import SymmetricKey, Opaque
except ImportError:
    print("ERROR: yubihsm Python library not found. Install with: pip install yubihsm")
    sys.exit(1)

class SEDSSDManager:
    """SED SSD Manager with YubiHSM integration"""

    def __init__(self, connector_url: str = "http://localhost:12345", auth_key: int = 2):
        self.connector_url = connector_url
        self.auth_key = auth_key
        self.config_dir = Path("/etc/yubihsm/sed-ssds")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = Path("/var/log/yubihsm-sed-ssd.log")

    def log(self, message: str) -> None:
        """Log message to file and stdout"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        print(log_entry)
        with open(self.log_file, 'a') as f:
            f.write(log_entry + '\n')

    def run_command(self, cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run shell command with logging"""
        self.log(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            self.log(f"Command failed: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
        return result

    def detect_sed_devices(self) -> List[str]:
        """Detect SED-capable devices"""
        self.log("Detecting SED-capable devices...")

        sed_devices = []

        # Use sedutil-cli to scan
        try:
            result = self.run_command(["sedutil-cli", "--scan"], check=False)
            for line in result.stdout.split('\n'):
                if 'TCG' in line and 'Device' in line:
                    # Extract device path
                    parts = line.split()
                    for part in parts:
                        if part.startswith('/dev/'):
                            sed_devices.append(part)
                            break
        except FileNotFoundError:
            self.log("sedutil-cli not found, trying alternative detection methods")

        # Alternative detection with hdparm
        try:
            # Get all block devices
            result = self.run_command(["lsblk", "-d", "-n", "-o", "NAME"])
            devices = [f"/dev/{dev.strip()}" for dev in result.stdout.split('\n') if dev.strip()]

            for device in devices:
                try:
                    result = self.run_command(["hdparm", "-I", device], check=False)
                    if "TCG Opal" in result.stdout:
                        if device not in sed_devices:
                            sed_devices.append(device)
                            self.log(f"Found SED device (hdparm): {device}")
                except Exception: 
                    continue
        except FileNotFoundError:
            self.log("hdparm not found")

        return sed_devices

    def get_device_info(self, device: str) -> Dict[str, str]:
        """Get detailed device information"""
        info = {"device": device, "serial": "unknown", "model": "unknown", "sed_standard": "unknown"}

        try:
            # Try sedutil-cli first
            result = self.run_command(["sedutil-cli", "--query", device], check=False)
            for line in result.stdout.split('\n'):
                if "Serial" in line:
                    info["serial"] = line.split(":")[-1].strip()
                elif "Model" in line:
                    info["model"] = line.split(":")[-1].strip()
                elif "TCG" in line:
                    info["sed_standard"] = "TCG Opal"
        except Exception: 
            pass

        # Fallback to hdparm
        if info["serial"] == "unknown":
            try:
                result = self.run_command(["hdparm", "-I", device], check=False)
                for line in result.stdout.split('\n'):
                    if "Serial Number" in line:
                        info["serial"] = line.split(":")[-1].strip()
                    elif "Model Number" in line:
                        info["model"] = line.split(":")[-1].strip()
            except Exception: 
                pass

        return info

    def generate_sed_key(self, device: str, password: str) -> Tuple[int, str]:
        """Generate SED authentication key in YubiHSM"""
        device_info = self.get_device_info(device)
        device_serial = device_info["serial"]

        # Create unique key ID based on device serial
        key_id = int(hashlib.sha256(f"sed-{device_serial}".encode()).hexdigest()[:8], 16) % 1000 + 6000

        self.log(f"Generating SED key for {device} (ID: {key_id})")

        # Connect to YubiHSM
        hsm = YubiHsm.connect(self.connector_url)
        session = hsm.create_session_derived(self.auth_key, password)

        # Generate AES-256 key for SED authentication
        key = SymmetricKey.generate(
            session=session,
            object_id=key_id,
            label=f"sed-auth-{device_serial}"[:40],
            domains=1,
            capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
            algorithm=ALGORITHM.AES256
        )

        session.close()

        # Generate derived key for SED password (deterministic for demo)
        derived_key = hashlib.sha256(f"{device_serial}-{key_id}-{password}".encode()).hex()

        return key_id, derived_key

    def initialize_sed_device(self, device: str, password: str, enable_mbr: bool = True) -> bool:
        """Initialize SED device with YubiHSM-backed authentication"""
        self.log(f"Initializing SED device: {device}")

        # Generate key in YubiHSM
        key_id, sed_password = self.generate_sed_key(device, password)

        # Initialize SED device
        try:
            self.run_command(["sedutil-cli", "--initialSetup", sed_password, device])

            if enable_mbr:
                self.run_command(["sedutil-cli", "--setMBRDone", "on", sed_password, device])

            # Store configuration
            config = {
                "device": device,
                "key_id": key_id,
                "initialized": datetime.now().isoformat(),
                "mbr_enabled": enable_mbr,
                "status": "initialized"
            }

            config_file = self.config_dir / f"{os.path.basename(device)}.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)

            self.log(f"SED device initialized successfully: {device}")
            return True

        except subprocess.CalledProcessError as e:
            self.log(f"Failed to initialize SED device: {e}")
            return False

    def unlock_sed_device(self, device: str, password: str) -> bool:
        """Unlock SED device using YubiHSM key"""
        config_file = self.config_dir / f"{os.path.basename(device)}.json"

        if not config_file.exists():
            self.log(f"Configuration not found for {device}")
            return False

        with open(config_file) as f:
            config = json.load(f)

        key_id = config["key_id"]

        # Get key from YubiHSM
        hsm = YubiHsm.connect(self.connector_url)
        session = hsm.create_session_derived(self.auth_key, password)

        try:
            key_obj = session.get_object(key_id, OBJECT.SYMMETRIC_KEY)
            # In production, properly derive the SED password from the key
            device_info = self.get_device_info(device)
            derived_key = hashlib.sha256(f"{device_info['serial']}-{key_id}-{password}".encode()).hex()
            sed_password = derived_key[:32]  # First 32 chars as password

            session.close()

            # Unlock device
            self.run_command(["sedutil-cli", "--setMBREnable", "on", sed_password, device])

            self.log(f"SED device unlocked: {device}")
            return True

        except Exception as e:
            self.log(f"Failed to unlock SED device: {e}")
            session.close()
            return False

    def lock_sed_device(self, device: str, password: str) -> bool:
        """Lock SED device"""
        config_file = self.config_dir / f"{os.path.basename(device)}.json"

        if not config_file.exists():
            self.log(f"Configuration not found for {device}")
            return False

        with open(config_file) as f:
            config = json.load(f)

        key_id = config["key_id"]

        # Get key from YubiHSM
        hsm = YubiHsm.connect(self.connector_url)
        session = hsm.create_session_derived(self.auth_key, password)

        try:
            key_obj = session.get_object(key_id, OBJECT.SYMMETRIC_KEY)
            device_info = self.get_device_info(device)
            derived_key = hashlib.sha256(f"{device_info['serial']}-{key_id}-{password}".encode()).hex()
            sed_password = derived_key[:32]

            session.close()

            # Lock device
            self.run_command(["sedutil-cli", "--setMBREnable", "off", sed_password, device])

            self.log(f"SED device locked: {device}")
            return True

        except Exception as e:
            self.log(f"Failed to lock SED device: {e}")
            session.close()
            return False

    def get_device_status(self, device: str) -> Dict[str, str]:
        """Get SED device status"""
        status = {"device": device, "lock_status": "unknown", "configured": "no"}

        config_file = self.config_dir / f"{os.path.basename(device)}.json"
        if config_file.exists():
            status["configured"] = "yes"
            with open(config_file) as f:
                config = json.load(f)
                status.update(config)

        # Check lock status
        try:
            result = self.run_command(["sedutil-cli", "--query", device], check=False)
            if "Locked = Y" in result.stdout:
                status["lock_status"] = "locked"
            elif "Locked = N" in result.stdout:
                status["lock_status"] = "unlocked"
        except Exception: 
            pass

        return status

    def list_configured_devices(self) -> List[Dict[str, str]]:
        """List all configured SED devices"""
        devices = []

        for config_file in self.config_dir.glob("*.json"):
            with open(config_file) as f:
                config = json.load(f)
                device = config["device"]
                status = self.get_device_status(device)
                devices.append(status)

        return devices

    def create_systemd_service(self, device: str) -> bool:
        """Create systemd service for automatic SED unlock"""
        config_file = self.config_dir / f"{os.path.basename(device)}.json"

        if not config_file.exists():
            self.log(f"Configuration not found for {device}")
            return False

        service_name = f"yubihsm-sed-{os.path.basename(device)}"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")

        service_content = f"""[Unit]
Description=YubiHSM SED Unlock for {device}
After=network.target yubihsm-connector.service
Before=local-fs.target
ConditionPathExists={device}

[Service]
Type=oneshot
RemainAfterExit=yes
# fail-fast: provide YUBIHSM_PASSWORD via EnvironmentFile (chmod 0600) — no hardcoded default
EnvironmentFile=-/etc/yubihsm/secrets.env
ExecStart=/usr/local/bin/yubihsm-sed-unlock {device}
ExecStop=/usr/local/bin/yubihsm-sed-lock {device}

[Install]
WantedBy=multi-user.target
"""

        try:
            with open(service_path, 'w') as f:
                f.write(service_content)

            self.run_command(["systemctl", "daemon-reload"])
            self.run_command(["systemctl", "enable", service_name])

            self.log(f"Created systemd service: {service_name}")
            return True

        except Exception as e:
            self.log(f"Failed to create systemd service: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description="YubiHSM 2 SED SSD Management")
    parser.add_argument("command", choices=[
        "detect", "init", "unlock", "lock", "status", "list", "service"
    ])
    parser.add_argument("--device", help="Device path (e.g., /dev/sdb)")
    parser.add_argument("--password", help="YubiHSM password")
    parser.add_argument("--connector", default="http://localhost:12345",
                       help="YubiHSM connector URL")
    parser.add_argument("--auth-key", type=int, default=2,
                       help="YubiHSM authentication key ID")
    parser.add_argument("--enable-mbr", action="store_true", default=True,
                       help="Enable MBR shadow (default: True)")

    args = parser.parse_args()

    # Get password if not provided
    password = args.password or os.getenv('YUBIHSM_PASSWORD')
    if not password and args.command in ['init', 'unlock', 'lock']:
        import getpass
        password = getpass.getpass("Enter YubiHSM password: ")

    manager = SEDSSDManager(args.connector, args.auth_key)

    try:
        if args.command == "detect":
            devices = manager.detect_sed_devices()
            print("Detected SED devices:")
            for device in devices:
                info = manager.get_device_info(device)
                print(f"  {device}: {info['model']} (Serial: {info['serial']})")

        elif args.command == "init":
            if not args.device:
                parser.error("--device required for init")
            success = manager.initialize_sed_device(args.device, password, args.enable_mbr)
            print("SUCCESS" if success else "FAILED")

        elif args.command == "unlock":
            if not args.device:
                parser.error("--device required for unlock")
            success = manager.unlock_sed_device(args.device, password)
            print("SUCCESS" if success else "FAILED")

        elif args.command == "lock":
            if not args.device:
                parser.error("--device required for lock")
            success = manager.lock_sed_device(args.device, password)
            print("SUCCESS" if success else "FAILED")

        elif args.command == "status":
            if not args.device:
                parser.error("--device required for status")
            status = manager.get_device_status(args.device)
            print(json.dumps(status, indent=2))

        elif args.command == "list":
            devices = manager.list_configured_devices()
            print(json.dumps(devices, indent=2))

        elif args.command == "service":
            if not args.device:
                parser.error("--device required for service")
            success = manager.create_systemd_service(args.device)
            print("SUCCESS" if success else "FAILED")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()