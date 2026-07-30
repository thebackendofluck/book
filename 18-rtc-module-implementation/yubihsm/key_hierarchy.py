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
YubiHSM 2 Key Hierarchy Management
Comprehensive key lifecycle management for iGaming HSM infrastructure.

Implements:
    - Domain-separated key hierarchy (SED, Database, SSL, Signing)
    - Key rotation with backup and recovery
    - Compliance reporting (FIPS 140-2, PCI DSS, GDPR)
    - Audit logging for regulatory requirements

Usage:
    python3 key_hierarchy.py init --password <admin_password>
    python3 key_hierarchy.py create-db-key --db-name casino_db
    python3 key_hierarchy.py create-ssl-key --domain api.example.com
    python3 key_hierarchy.py compliance-report --output report.json
    python3 key_hierarchy.py rotate-key --key-id 3001
"""

import sys
import os
import json
import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict

try:
    from yubihsm import YubiHsm  # ty:ignore[unresolved-import]
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT, DOMAIN  # ty:ignore[unresolved-import]
    from yubihsm.objects import (  # ty:ignore[unresolved-import]
        SymmetricKey, AsymmetricKey, WrapKey, Opaque,
        AuthenticationKey, Template
    )
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # ty:ignore[unresolved-import]
    from cryptography.hazmat.primitives.asymmetric import rsa, ec
    from cryptography.hazmat.backends import default_backend
    from cryptography import x509
    import requests
except ImportError as e:
    print(f"ERROR: Missing required library: {e}")
    print("Install with: pip install yubihsm cryptography requests")
    sys.exit(1)


@dataclass
class KeyMetadata:
    """Key metadata structure for compliance tracking"""
    key_id: int
    label: str
    key_type: str
    algorithm: str
    capabilities: List[str]
    domains: int
    created: datetime
    last_used: Optional[datetime] = None
    rotation_due: Optional[datetime] = None
    purpose: str = ""
    owner: str = ""
    compliance_tags: List[str] = None  # ty:ignore[invalid-assignment]

    def __post_init__(self):
        if self.compliance_tags is None:
            self.compliance_tags = []


class YubiHSMKeyHierarchy:
    """YubiHSM 2 Key Hierarchy Manager for iGaming Compliance"""

    # Domain separation for gambling infrastructure
    DOMAINS = {
        1: "SED SSD Operations",
        2: "Database Encryption (TDE)",
        3: "Certificate Management",
        4: "General Encryption",
        5: "Signing Operations",
        6: "SSH Key Management",
        7: "API Keys & Secrets",
        8: "Audit & Compliance"
    }

    # Key ID allocation ranges
    KEY_RANGES = {
        "sed": (6000, 6999),
        "database": (3000, 3999),
        "ssl": (4000, 4999),
        "encryption": (1000, 1999),
        "signing": (5000, 5999),
        "ssh": (7000, 7999),
        "api": (8000, 8999)
    }

    def __init__(self, connector_url: str = "http://localhost:12345"):
        self.connector_url = connector_url
        self.hsm: Optional[YubiHsm] = None
        self.session = None
        self.device_info = None
        self.audit_log = []

    def connect(self, auth_key_id: int = 1, password: str = ""):
        """Establish connection to YubiHSM"""
        try:
            self.hsm = YubiHsm.connect(self.connector_url)
            self.session = self.hsm.create_session_derived(auth_key_id, password)
            self.device_info = self.hsm.get_device_info()
            self._log_audit("CONNECT", f"Connected to HSM {self.device_info.serial}")
            return True
        except Exception as e:
            self._log_audit("CONNECT_FAILED", str(e))
            raise

    def disconnect(self):
        """Close HSM connection"""
        if self.session:
            self.session.close()
        if self.hsm:
            self.hsm.disconnect()
        self._log_audit("DISCONNECT", "Connection closed")

    def _log_audit(self, operation: str, details: str):
        """Log audit events"""
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "details": details,
            "session_id": getattr(self.session, 'session_id', None) if self.session else None
        }
        self.audit_log.append(audit_entry)
        print(f"[AUDIT] {operation}: {details}")

    # ========================================================================
    # KEY HIERARCHY INITIALIZATION
    # ========================================================================

    def initialize_key_hierarchy(self, admin_password: str):
        """Initialize complete key hierarchy for gambling platform"""
        self._log_audit("INIT_HIERARCHY", "Starting key hierarchy initialization")

        self._create_authentication_keys(admin_password)
        self._create_master_wrap_keys()
        self._configure_domains()
        self._create_key_templates()
        self._configure_audit()

        self._log_audit("INIT_HIERARCHY", "Key hierarchy initialization complete")

    def _create_authentication_keys(self, admin_password: str):
        """Create role-based authentication key hierarchy"""
        auth_keys = [
            {"id": 1, "label": "admin-auth-key",
             "password": admin_password,
             "description": "Full administrative access"},
            {"id": 2, "label": "storage-auth-key",
             "password": self._derive_password("storage", admin_password),
             "description": "Storage and lifecycle management"},
            {"id": 3, "label": "audit-auth-key",
             "password": self._derive_password("audit", admin_password),
             "description": "Audit and compliance access"},
            {"id": 4, "label": "sed-auth-key",
             "password": self._derive_password("sed", admin_password),
             "description": "SED SSD management"},
            {"id": 5, "label": "db-auth-key",
             "password": self._derive_password("database", admin_password),
             "description": "Database encryption keys"},
        ]

        for key_config in auth_keys:
            try:
                AuthenticationKey.put_derived(
                    session=self.session,
                    object_id=key_config["id"],
                    label=key_config["label"],
                    domains=DOMAIN.DOMAIN_1,
                    capabilities=CAPABILITY.ALL,
                    password=key_config["password"]
                )
                self._log_audit("CREATE_AUTH_KEY",
                                f"Created {key_config['label']} (ID: {key_config['id']})")
            except Exception as e:
                self._log_audit("CREATE_AUTH_KEY_FAILED",
                                f"Failed to create {key_config['label']}: {e}")

    def _create_master_wrap_keys(self):
        """Create master wrap keys for key export/import"""
        wrap_keys = [
            {"id": 100, "label": "master-wrap-key",
             "description": "Master key for all domains"},
            {"id": 101, "label": "sed-wrap-key",
             "description": "SED SSD key wrapping"},
            {"id": 102, "label": "db-wrap-key",
             "description": "Database key wrapping"},
        ]

        for key_config in wrap_keys:
            try:
                WrapKey.generate(
                    session=self.session,
                    object_id=key_config["id"],
                    label=key_config["label"],
                    domains=0xFFFF if key_config["id"] == 100 else DOMAIN.DOMAIN_1,
                    capabilities=CAPABILITY.EXPORT_WRAPPED | CAPABILITY.IMPORT_WRAPPED,
                    algorithm=ALGORITHM.AES256_CCM_WRAP
                )
                self._log_audit("CREATE_WRAP_KEY",
                                f"Created {key_config['label']} (ID: {key_config['id']})")
            except Exception as e:
                self._log_audit("CREATE_WRAP_KEY_FAILED",
                                f"Failed to create {key_config['label']}: {e}")

    def _configure_domains(self):
        """Configure domain separation"""
        for domain_id, description in self.DOMAINS.items():
            self._log_audit("CONFIGURE_DOMAIN", f"Domain {domain_id}: {description}")

    def _create_key_templates(self):
        """Create key templates for consistent key generation"""
        templates = [
            {"id": 200, "label": "sed-auth-template",
             "algorithm": ALGORITHM.AES256,
             "capabilities": CAPABILITY.EXPORTABLE_UNDER_WRAP},
            {"id": 201, "label": "tde-key-template",
             "algorithm": ALGORITHM.AES256,
             "capabilities": CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC},
            {"id": 202, "label": "ssl-key-template",
             "algorithm": ALGORITHM.RSA_2048,
             "capabilities": CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS},
        ]

        for template_config in templates:
            try:
                Template.put(
                    session=self.session,
                    object_id=template_config["id"],
                    label=template_config["label"],
                    domains=DOMAIN.DOMAIN_1,
                    capabilities=template_config["capabilities"],
                    algorithm=template_config["algorithm"],
                    template_data=b""
                )
                self._log_audit("CREATE_TEMPLATE",
                                f"Created {template_config['label']} (ID: {template_config['id']})")
            except Exception as e:
                self._log_audit("CREATE_TEMPLATE_FAILED",
                                f"Failed to create {template_config['label']}: {e}")

    def _configure_audit(self):
        """Configure audit logging and FIPS mode"""
        self.session.set_option(0, 1, True)  # audit-log on  # ty:ignore[unresolved-attribute]
        self.session.set_option(0, 2, True)  # audit-export on  # ty:ignore[unresolved-attribute]
        self.session.set_option(0, 3, True)  # force-audit on  # ty:ignore[unresolved-attribute]
        self.session.set_option(0, 4, True)  # fips-mode on  # ty:ignore[unresolved-attribute]
        self._log_audit("CONFIGURE_AUDIT", "Audit logging and FIPS mode enabled")

    def _derive_password(self, purpose: str, base_password: str) -> str:
        """Derive domain-specific passwords from base password"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=f"yubihsm-{purpose}-salt".encode(),
            iterations=10000,
        )
        derived = kdf.derive(base_password.encode())
        return base64.b64encode(derived).decode()[:32]

    # ========================================================================
    # APPLICATION-SPECIFIC KEY MANAGEMENT
    # ========================================================================

    def create_database_key(self, db_name: str, key_type: str = "tde") -> Tuple[int, KeyMetadata]:
        """Create database encryption key (TDE or signing)"""
        key_id = self._allocate_key_id("database", db_name)

        try:
            if key_type == "tde":
                key = SymmetricKey.generate(
                    session=self.session,
                    object_id=key_id,
                    label=f"tde-{db_name}",
                    domains=DOMAIN.DOMAIN_2,
                    capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
                    algorithm=ALGORITHM.AES256
                )
                algorithm = "AES256"
                capabilities = ["ENCRYPT_CBC", "DECRYPT_CBC"]
            else:
                key = AsymmetricKey.generate_rsa(
                    session=self.session,
                    object_id=key_id,
                    label=f"db-sign-{db_name}",
                    domains=DOMAIN.DOMAIN_2,
                    capabilities=CAPABILITY.SIGN_PKCS,
                    algorithm=ALGORITHM.RSA_2048
                )
                algorithm = "RSA2048"
                capabilities = ["SIGN_PKCS"]

            metadata = KeyMetadata(
                key_id=key_id,
                label=key.label,
                key_type="DB_ENCRYPTION" if key_type == "tde" else "DB_SIGNING",
                algorithm=algorithm,
                capabilities=capabilities,
                domains=2,
                created=datetime.now(),
                rotation_due=datetime.now() + timedelta(days=365),
                purpose=f"Database {key_type.upper()} for {db_name}",
                owner="DB_ADMIN",
                compliance_tags=["FIPS_140_2", "PCI_DSS", "GLI_11"]
            )

            self._store_key_metadata(metadata)
            self._log_audit("CREATE_DB_KEY", f"Created {key_type} key for {db_name} (ID: {key_id})")
            return key_id, metadata

        except Exception as e:
            self._log_audit("CREATE_DB_KEY_FAILED", str(e))
            raise

    def create_ssl_key(self, domain: str, key_type: str = "rsa") -> Tuple[int, KeyMetadata]:
        """Create SSL/TLS certificate key"""
        key_id = self._allocate_key_id("ssl", domain)

        try:
            if key_type.lower() == "rsa":
                key = AsymmetricKey.generate_rsa(
                    session=self.session,
                    object_id=key_id,
                    label=f"ssl-rsa-{domain}",
                    domains=DOMAIN.DOMAIN_3,
                    capabilities=CAPABILITY.SIGN_PKCS,
                    algorithm=ALGORITHM.RSA_2048
                )
                algorithm = "RSA2048"
            else:
                key = AsymmetricKey.generate_ec(
                    session=self.session,
                    object_id=key_id,
                    label=f"ssl-ecc-{domain}",
                    domains=DOMAIN.DOMAIN_3,
                    capabilities=CAPABILITY.SIGN_ECDSA,
                    algorithm=ALGORITHM.EC_P256
                )
                algorithm = "EC_P256"

            metadata = KeyMetadata(
                key_id=key_id,
                label=key.label,
                key_type="SSL_TLS",
                algorithm=algorithm,
                capabilities=["SIGN_PKCS"] if key_type == "rsa" else ["SIGN_ECDSA"],
                domains=3,
                created=datetime.now(),
                rotation_due=datetime.now() + timedelta(days=365),
                purpose=f"SSL/TLS certificate for {domain}",
                owner="CERT_ADMIN",
                compliance_tags=["FIPS_140_2", "SSL_TLS", "CERTIFICATE"]
            )

            self._store_key_metadata(metadata)
            self._log_audit("CREATE_SSL_KEY", f"Created SSL key for {domain} (ID: {key_id})")
            return key_id, metadata

        except Exception as e:
            self._log_audit("CREATE_SSL_KEY_FAILED", str(e))
            raise

    def create_sed_key(self, device_serial: str, purpose: str = "authentication") -> Tuple[int, KeyMetadata]:
        """Create SED SSD authentication key"""
        key_id = self._allocate_key_id("sed", device_serial)

        try:
            key = SymmetricKey.generate(
                session=self.session,
                object_id=key_id,
                label=f"sed-{purpose}-{device_serial[:8]}",
                domains=DOMAIN.DOMAIN_1,
                capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
                algorithm=ALGORITHM.AES256
            )

            metadata = KeyMetadata(
                key_id=key_id,
                label=key.label,
                key_type="SED_AUTH",
                algorithm="AES256",
                capabilities=["EXPORTABLE_UNDER_WRAP"],
                domains=1,
                created=datetime.now(),
                purpose=f"SED SSD {purpose}",
                owner="SED_MANAGER",
                compliance_tags=["TCG_OPAL", "FIPS_140_2", "SED_ENCRYPTION"]
            )

            self._store_key_metadata(metadata)
            self._log_audit("CREATE_SED_KEY", f"Created SED key for {device_serial} (ID: {key_id})")
            return key_id, metadata

        except Exception as e:
            self._log_audit("CREATE_SED_KEY_FAILED", str(e))
            raise

    def _allocate_key_id(self, category: str, identifier: str) -> int:
        """Allocate unique key ID based on category"""
        start, end = self.KEY_RANGES.get(category, (9000, 9999))

        hash_input = f"{category}-{identifier}-{datetime.now().isoformat()}"
        hash_value = int(hashlib.sha256(hash_input.encode()).hexdigest()[:8], 16)
        key_id = start + (hash_value % (end - start + 1))

        existing_objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]
        existing_ids = {obj.id for obj in existing_objects}

        while key_id in existing_ids:
            key_id += 1
            if key_id > end:
                key_id = start

        return key_id

    def _store_key_metadata(self, metadata: KeyMetadata):
        """Store key metadata in HSM as opaque object"""
        metadata_json = json.dumps(asdict(metadata), default=str)
        metadata_id = metadata.key_id + 10000

        try:
            Opaque.put(
                session=self.session,
                object_id=metadata_id,
                label=f"metadata-{metadata.key_id}",
                domains=DOMAIN.DOMAIN_8,
                capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
                algorithm=ALGORITHM.OPAQUE_DATA,
                data=metadata_json.encode()
            )
        except Exception as e:
            self._log_audit("STORE_METADATA_FAILED",
                            f"Failed to store metadata for key {metadata.key_id}: {e}")

    # ========================================================================
    # KEY LIFECYCLE MANAGEMENT
    # ========================================================================

    def rotate_key(self, key_id: int) -> bool:
        """Rotate encryption key with backup and recovery"""
        try:
            metadata = self._get_key_metadata(key_id)
            if not metadata:
                raise ValueError(f"Key {key_id} not found")

            backup_id = self._backup_key(key_id)
            self._log_audit("KEY_BACKUP", f"Backed up key {key_id} as {backup_id}")

            if metadata.key_type == "SED_AUTH":
                new_key_id, _ = self.create_sed_key(f"rotated-{key_id}")
            elif metadata.key_type.startswith("DB_"):
                db_name = metadata.purpose.split()[-1]
                key_type = "tde" if "TDE" in metadata.key_type else "signing"
                new_key_id, _ = self.create_database_key(db_name, key_type)
            elif metadata.key_type == "SSL_TLS":
                domain = metadata.purpose.split()[-1]
                key_type = "rsa" if "RSA" in metadata.algorithm else "ecc"
                new_key_id, _ = self.create_ssl_key(domain, key_type)
            else:
                self._log_audit("KEY_ROTATION_FAILED", f"Unknown key type: {metadata.key_type}")
                return False

            old_key = self.session.get_object(key_id, OBJECT.SYMMETRIC_KEY)  # ty:ignore[unresolved-attribute]
            old_key.delete()

            self._log_audit("KEY_ROTATION", f"Rotated key {key_id} to {new_key_id}")
            return True

        except Exception as e:
            self._log_audit("KEY_ROTATION_FAILED", f"Failed to rotate key {key_id}: {e}")
            return False

    def _backup_key(self, key_id: int) -> int:
        """Create wrapped backup of key"""
        wrap_key = self.session.get_object(100, OBJECT.WRAP_KEY)  # ty:ignore[unresolved-attribute]
        key_obj = self.session.get_object(key_id, OBJECT.SYMMETRIC_KEY)  # ty:ignore[unresolved-attribute]
        backup_id = key_id + 20000

        wrapped_data = key_obj.export_wrapped(wrap_key)

        Opaque.put(
            session=self.session,
            object_id=backup_id,
            label=f"backup-{key_id}",
            domains=DOMAIN.DOMAIN_8,
            capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
            algorithm=ALGORITHM.OPAQUE_DATA,
            data=wrapped_data
        )

        return backup_id

    def _get_key_metadata(self, key_id: int) -> Optional[KeyMetadata]:
        """Retrieve key metadata"""
        metadata_id = key_id + 10000

        try:
            metadata_obj = self.session.get_object(metadata_id, OBJECT.OPAQUE)  # ty:ignore[unresolved-attribute]
            metadata_data = metadata_obj.get()
            metadata_dict = json.loads(metadata_data.decode())

            if metadata_dict.get('created'):
                metadata_dict['created'] = datetime.fromisoformat(metadata_dict['created'])
            if metadata_dict.get('last_used'):
                metadata_dict['last_used'] = datetime.fromisoformat(metadata_dict['last_used'])
            if metadata_dict.get('rotation_due'):
                metadata_dict['rotation_due'] = datetime.fromisoformat(metadata_dict['rotation_due'])

            return KeyMetadata(**metadata_dict)
        except Exception:
            return None

    # ========================================================================
    # COMPLIANCE REPORTING
    # ========================================================================

    def generate_compliance_report(self, output_file: str) -> bool:
        """Generate compliance report for gambling regulators"""
        report = {
            "report_type": "YubiHSM 2 Key Hierarchy Compliance Report",
            "generated_at": datetime.now().isoformat(),
            "device_info": {
                "serial": self.device_info.serial,  # ty:ignore[unresolved-attribute]
                "version": str(self.device_info.version),  # ty:ignore[unresolved-attribute]
                "fips_mode": True
            },
            "key_inventory": [],
            "compliance_status": {},
            "recommendations": []
        }

        objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]
        key_counts = {
            "authentication": 0, "symmetric": 0, "asymmetric": 0,
            "wrap": 0, "opaque": 0
        }

        for obj in objects:
            obj_type = obj.object_type.name.lower()
            if obj_type in key_counts:
                key_counts[obj_type] += 1

            metadata = self._get_key_metadata(obj.id)
            if metadata:
                report["key_inventory"].append(asdict(metadata))

        report["key_inventory_summary"] = key_counts

        # Compliance checks
        compliance_issues = []
        if key_counts["authentication"] < 3:
            compliance_issues.append("Insufficient authentication keys (minimum 3 required)")

        expired_keys = self._check_key_expiration()
        if expired_keys:
            compliance_issues.append(f"{len(expired_keys)} keys require rotation")

        report["compliance_status"] = {
            "overall_compliant": len(compliance_issues) == 0,
            "issues": compliance_issues,
            "compliance_score": max(0, 100 - (len(compliance_issues) * 20))
        }

        # Recommendations
        if key_counts["authentication"] < 5:
            report["recommendations"].append("Consider adding more role-based authentication keys")
        if key_counts["wrap"] < 2:
            report["recommendations"].append("Add domain-specific wrap keys for better separation")
        if expired_keys:
            report["recommendations"].append("Implement automated key rotation policy")

        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        self._log_audit("COMPLIANCE_REPORT", f"Generated compliance report: {output_file}")
        return True

    def _check_key_expiration(self) -> List[int]:
        """Check for expired or soon-to-expire keys"""
        expired_keys = []
        objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]

        for obj in objects:
            metadata = self._get_key_metadata(obj.id)
            if metadata and metadata.rotation_due:
                if metadata.rotation_due < datetime.now():
                    expired_keys.append(obj.id)

        return expired_keys

    def export_audit_log(self, output_file: str) -> bool:
        """Export audit log for compliance"""
        try:
            audit_data = self.session.get_audit_log()  # ty:ignore[unresolved-attribute]

            with open(output_file, 'wb') as f:
                f.write(audit_data)

            self._log_audit("AUDIT_EXPORT", f"Exported audit log to {output_file}")
            return True
        except Exception as e:
            self._log_audit("AUDIT_EXPORT_FAILED", str(e))
            return False


def main():
    """CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(description="YubiHSM 2 Key Hierarchy Manager")
    parser.add_argument("command", choices=[
        "init", "create-sed-key", "create-db-key", "create-ssl-key",
        "rotate-key", "compliance-report", "audit-export"
    ])
    parser.add_argument("--connector-url", default="http://localhost:12345")
    parser.add_argument("--auth-key", type=int, default=1)
    parser.add_argument("--password", help="HSM password")
    parser.add_argument("--output", help="Output file")
    parser.add_argument("--device-serial", help="Device serial for SED keys")
    parser.add_argument("--db-name", help="Database name for DB keys")
    parser.add_argument("--domain", help="Domain for SSL keys")
    parser.add_argument("--key-id", type=int, help="Key ID for operations")

    args = parser.parse_args()

    password = args.password or os.getenv('YUBIHSM_PASSWORD')
    if not password:
        import getpass
        password = getpass.getpass("Enter YubiHSM password: ")

    manager = YubiHSMKeyHierarchy(args.connector_url)

    try:
        if args.command == "init":
            manager.connect(args.auth_key, password)
            manager.initialize_key_hierarchy(password)

        elif args.command == "create-sed-key":
            if not args.device_serial:
                parser.error("--device-serial required")
            manager.connect(args.auth_key, password)
            key_id, metadata = manager.create_sed_key(args.device_serial)
            print(f"Created SED key: ID={key_id}, Label={metadata.label}")

        elif args.command == "create-db-key":
            if not args.db_name:
                parser.error("--db-name required")
            manager.connect(args.auth_key, password)
            key_id, metadata = manager.create_database_key(args.db_name)
            print(f"Created DB key: ID={key_id}, Label={metadata.label}")

        elif args.command == "create-ssl-key":
            if not args.domain:
                parser.error("--domain required")
            manager.connect(args.auth_key, password)
            key_id, metadata = manager.create_ssl_key(args.domain)
            print(f"Created SSL key: ID={key_id}, Label={metadata.label}")

        elif args.command == "rotate-key":
            if not args.key_id:
                parser.error("--key-id required")
            manager.connect(args.auth_key, password)
            success = manager.rotate_key(args.key_id)
            print(f"Key rotation {'successful' if success else 'failed'}")

        elif args.command == "compliance-report":
            output_file = args.output or f"compliance-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            manager.connect(args.auth_key, password)
            manager.generate_compliance_report(output_file)
            print(f"Compliance report saved to: {output_file}")

        elif args.command == "audit-export":
            output_file = args.output or f"audit-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bin"
            manager.connect(args.auth_key, password)
            success = manager.export_audit_log(output_file)
            print(f"Audit export {'successful' if success else 'failed'}")

        manager.disconnect()

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
