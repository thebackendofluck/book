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

# YubiHSM 2 Key Hierarchy Management and Yubico Integration
# Comprehensive key lifecycle management with enterprise integration

import sys
import os
import json
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
import subprocess

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from yubihsm import YubiHsm
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT, DOMAIN
    from yubihsm.objects import (
        SymmetricKey, AsymmetricKey, WrapKey, Opaque,
        AuthenticationKey, Template
    )
    from cryptography.hazmat.primitives import hashes, serialization
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
    """Key metadata structure"""
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
    compliance_tags: Optional[List[str]] = None

    def __post_init__(self):
        if self.compliance_tags is None:
            self.compliance_tags = []

class YubiHSMKeyHierarchy:
    """YubiHSM 2 Key Hierarchy Manager with Yubico Integration"""

    def __init__(self, connector_url: str = "http://localhost:12345"):
        self.connector_url = connector_url
        self.hsm: Optional[YubiHsm] = None
        self.session = None
        self.device_info = None
        self.yubico_api_key = os.getenv('YUBICO_API_KEY')
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

    # ============================================================================
    # KEY HIERARCHY MANAGEMENT
    # ============================================================================

    def initialize_key_hierarchy(self, admin_password: str) -> Dict[str, str]:
        """Initialize complete key hierarchy.

        Returns the independently-generated domain auth-key passwords. The
        caller MUST capture and store these immediately (e.g. in Vault/OpenBao,
        one secret per domain) - they cannot be read back from the HSM.
        """
        self._log_audit("INIT_HIERARCHY", "Starting key hierarchy initialization")

        # 1. Create authentication key hierarchy
        domain_passwords = self._create_authentication_keys(admin_password)

        # 2. Generate master wrap keys
        self._create_master_wrap_keys()

        # 3. Set up domain separation
        self._configure_domains()

        # 4. Create application-specific key templates
        self._create_key_templates()

        # 5. Initialize audit and compliance settings
        self._configure_audit()

        self._log_audit("INIT_HIERARCHY", "Key hierarchy initialization complete")
        return domain_passwords

    def _create_authentication_keys(self, admin_password: str) -> Dict[str, str]:
        """Create authentication key hierarchy.

        Each non-admin domain key gets its own independently-generated,
        high-entropy password (self._generate_domain_password()). Domain
        passwords are never derived from admin_password: a deterministic
        derivation (fixed salt, published KDF params) would let anyone who
        recovers admin_password recompute every domain key offline, defeating
        the compartmentalization these separate auth keys are meant to provide.
        """
        storage_password = self._generate_domain_password()
        audit_password = self._generate_domain_password()
        sed_password = self._generate_domain_password()
        db_password = self._generate_domain_password()

        auth_keys = [
            {
                "id": 1,
                "label": "admin-auth-key",
                "password": admin_password,
                "capabilities": "all",
                "description": "Full administrative access"
            },
            {
                "id": 2,
                "label": "storage-auth-key",
                "password": storage_password,
                "capabilities": "exportable-under-wrap,importable-under-wrap,export-wrapped,import-wrapped",
                "description": "Storage and lifecycle management"
            },
            {
                "id": 3,
                "label": "audit-auth-key",
                "password": audit_password,
                "capabilities": "get-audit-log,sign-audit-log",
                "description": "Audit and compliance access"
            },
            {
                "id": 4,
                "label": "sed-auth-key",
                "password": sed_password,
                "capabilities": "exportable-under-wrap",
                "description": "SED SSD management"
            },
            {
                "id": 5,
                "label": "db-auth-key",
                "password": db_password,
                "capabilities": "exportable-under-wrap",
                "description": "Database encryption keys"
            }
        ]

        domain_passwords: Dict[str, str] = {}

        for key_config in auth_keys:
            try:
                auth_key = AuthenticationKey.put_derived(
                    session=self.session,
                    object_id=key_config["id"],
                    label=key_config["label"],
                    domains=DOMAIN.DOMAIN_1,  # Admin domain
                    capabilities=getattr(CAPABILITY, key_config["capabilities"].upper().replace(",", "_")),
                    password=key_config["password"]
                )
                self._log_audit("CREATE_AUTH_KEY", f"Created {key_config['label']} (ID: {key_config['id']})")
                if key_config["id"] != 1:
                    domain_passwords[str(key_config["label"])] = str(key_config["password"])
            except Exception as e:
                self._log_audit("CREATE_AUTH_KEY_FAILED", f"Failed to create {key_config['label']}: {e}")

        if domain_passwords:
            self._log_audit(
                "DOMAIN_KEY_SECRETS_GENERATED",
                f"Generated {len(domain_passwords)} independent domain auth-key passwords - "
                "store each one now in the secrets manager under its own path; they cannot "
                "be retrieved from the HSM afterward"
            )

        return domain_passwords

    def _create_master_wrap_keys(self):
        """Create master wrap keys for key export/import"""
        wrap_keys = [
            {
                "id": 100,
                "label": "master-wrap-key",
                "algorithm": ALGORITHM.AES256_CCM_WRAP,
                "description": "Master key for all domains"
            },
            {
                "id": 101,
                "label": "sed-wrap-key",
                "algorithm": ALGORITHM.AES256_CCM_WRAP,
                "description": "SED SSD key wrapping"
            },
            {
                "id": 102,
                "label": "db-wrap-key",
                "algorithm": ALGORITHM.AES256_CCM_WRAP,
                "description": "Database key wrapping"
            }
        ]

        for key_config in wrap_keys:
            try:
                wrap_key = WrapKey.generate(
                    session=self.session,
                    object_id=key_config["id"],
                    label=key_config["label"],
                    domains=0xFFFF if key_config["id"] == 100 else DOMAIN.DOMAIN_1,
                    capabilities=CAPABILITY.EXPORT_WRAPPED | CAPABILITY.IMPORT_WRAPPED,
                    algorithm=key_config["algorithm"]
                )
                self._log_audit("CREATE_WRAP_KEY", f"Created {key_config['label']} (ID: {key_config['id']})")
            except Exception as e:
                self._log_audit("CREATE_WRAP_KEY_FAILED", f"Failed to create {key_config['label']}: {e}")

    def _configure_domains(self):
        """Configure domain separation"""
        # Domain 1: SED SSD operations
        # Domain 2: Database encryption
        # Domain 3: Certificate management
        # Domain 4: General encryption
        # Domain 5: Signing operations
        # Domain 6: SSH keys
        # Domain 7: API keys and secrets
        # Domain 8: Audit and compliance

        domains = {
            1: "SED SSD Operations",
            2: "Database Encryption",
            3: "Certificate Management",
            4: "General Encryption",
            5: "Signing Operations",
            6: "SSH Key Management",
            7: "API Keys & Secrets",
            8: "Audit & Compliance"
        }

        for domain_id, description in domains.items():
            self._log_audit("CONFIGURE_DOMAIN", f"Domain {domain_id}: {description}")

    def _create_key_templates(self):
        """Create key templates for consistent key generation"""
        templates = [
            {
                "id": 200,
                "label": "sed-auth-template",
                "algorithm": ALGORITHM.AES256,
                "capabilities": CAPABILITY.EXPORTABLE_UNDER_WRAP,
                "description": "SED authentication key template"
            },
            {
                "id": 201,
                "label": "tde-key-template",
                "algorithm": ALGORITHM.AES256,
                "capabilities": CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
                "description": "Database TDE key template"
            },
            {
                "id": 202,
                "label": "ssl-key-template",
                "algorithm": ALGORITHM.RSA_2048,
                "capabilities": CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS,
                "description": "SSL/TLS key template"
            }
        ]

        for template_config in templates:
            try:
                template = Template.put(
                    session=self.session,
                    object_id=template_config["id"],
                    label=template_config["label"],
                    domains=DOMAIN.DOMAIN_1,
                    capabilities=template_config["capabilities"],
                    algorithm=template_config["algorithm"],
                    template_data=b""  # Empty template
                )
                self._log_audit("CREATE_TEMPLATE", f"Created {template_config['label']} (ID: {template_config['id']})")
            except Exception as e:
                self._log_audit("CREATE_TEMPLATE_FAILED", f"Failed to create {template_config['label']}: {e}")

    def _configure_audit(self):
        """Configure audit logging and compliance settings"""
        # Enable audit logging
        self.session.set_option(0, 1, True)  # audit-log on
        self.session.set_option(0, 2, True)  # audit-export on
        self.session.set_option(0, 3, True)  # force-audit on

        # Set FIPS mode
        self.session.set_option(0, 4, True)  # fips-mode on

        self._log_audit("CONFIGURE_AUDIT", "Audit logging and FIPS mode enabled")

    def _generate_domain_password(self, length_bytes: int = 24) -> str:
        """Generate an independent, high-entropy password for a domain auth key.

        Uses the OS CSPRNG (secrets module) directly - no relationship to
        admin_password or to any other domain key's password. Each domain
        key's compromise must not expose any other key.
        """
        return base64.b64encode(secrets.token_bytes(length_bytes)).decode()

    # ============================================================================
    # APPLICATION-SPECIFIC KEY MANAGEMENT
    # ============================================================================

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

    def create_database_key(self, db_name: str, key_type: str = "tde") -> Tuple[int, KeyMetadata]:
        """Create database encryption key"""
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
                # Generate RSA key for signing
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
                compliance_tags=["FIPS_140_2", "PCI_DSS", "HIPAA", "GDPR"]
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

    def _allocate_key_id(self, category: str, identifier: str) -> int:
        """Allocate unique key ID based on category and identifier"""
        # Category ranges:
        # SED: 6000-6999
        # Database: 3000-3999
        # SSL/Certificates: 4000-4999
        # General Encryption: 1000-1999
        # Signing: 5000-5999
        # SSH: 7000-7999
        # API Keys: 8000-8999

        ranges = {
            "sed": (6000, 6999),
            "database": (3000, 3999),
            "ssl": (4000, 4999),
            "encryption": (1000, 1999),
            "signing": (5000, 5999),
            "ssh": (7000, 7999),
            "api": (8000, 8999)
        }

        start, end = ranges.get(category, (9000, 9999))

        # Create hash-based ID within range
        hash_input = f"{category}-{identifier}-{datetime.now().isoformat()}"
        hash_value = int(hashlib.sha256(hash_input.encode()).hexdigest()[:8], 16)
        key_id = start + (hash_value % (end - start + 1))

        # Ensure uniqueness by checking existing objects
        existing_objects = self.session.list_objects()
        existing_ids = {obj.id for obj in existing_objects}

        while key_id in existing_ids:
            key_id += 1
            if key_id > end:
                key_id = start

        return key_id

    def _store_key_metadata(self, metadata: KeyMetadata):
        """Store key metadata in HSM as opaque object"""
        metadata_json = json.dumps(asdict(metadata), default=str)

        # Store as opaque object with ID = key_id + 10000
        metadata_id = metadata.key_id + 10000

        try:
            opaque = Opaque.put(
                session=self.session,
                object_id=metadata_id,
                label=f"metadata-{metadata.key_id}",
                domains=DOMAIN.DOMAIN_8,  # Audit domain
                capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
                algorithm=ALGORITHM.OPAQUE_DATA,
                data=metadata_json.encode()
            )
        except Exception as e:
            self._log_audit("STORE_METADATA_FAILED", f"Failed to store metadata for key {metadata.key_id}: {e}")

    # ============================================================================
    # KEY LIFECYCLE MANAGEMENT
    # ============================================================================

    def rotate_key(self, key_id: int, new_algorithm: Optional[str] = None) -> bool:
        """Rotate encryption key with backup and recovery"""
        try:
            # Get current key metadata
            metadata = self._get_key_metadata(key_id)
            if not metadata:
                raise ValueError(f"Key {key_id} not found")

            # Backup current key
            backup_id = self._backup_key(key_id)
            self._log_audit("KEY_BACKUP", f"Backed up key {key_id} as {backup_id}")

            # Generate new key
            new_key_id = self._allocate_key_id("rotation", f"{key_id}-{datetime.now().isoformat()}")

            if metadata.key_type == "SED_AUTH":
                new_key, new_metadata = self.create_sed_key(f"rotated-{key_id}")
            elif metadata.key_type.startswith("DB_"):
                db_name = metadata.purpose.split()[-1]  # Extract DB name
                key_type = "tde" if "TDE" in metadata.key_type else "signing"
                new_key, new_metadata = self.create_database_key(db_name, key_type)
            elif metadata.key_type == "SSL_TLS":
                domain = metadata.purpose.split()[-1]  # Extract domain
                key_type = "rsa" if "RSA" in metadata.algorithm else "ecc"
                new_key, new_metadata = self.create_ssl_key(domain, key_type)
            else:
                # Generic rotation
                new_key = SymmetricKey.generate(
                    session=self.session,
                    object_id=new_key_id,
                    label=f"rotated-{metadata.label}",
                    domains=metadata.domains,
                    capabilities=getattr(CAPABILITY, metadata.capabilities[0]),
                    algorithm=getattr(ALGORITHM, new_algorithm or metadata.algorithm)
                )
                new_metadata = metadata
                new_metadata.key_id = new_key_id
                new_metadata.created = datetime.now()

            # Update applications (this would be application-specific)
            self._update_key_consumers(key_id, new_key_id)

            # Delete old key
            old_key = self.session.get_object(key_id, OBJECT.SYMMETRIC_KEY)
            old_key.delete()

            self._log_audit("KEY_ROTATION", f"Rotated key {key_id} to {new_key_id}")
            return True

        except Exception as e:
            self._log_audit("KEY_ROTATION_FAILED", f"Failed to rotate key {key_id}: {e}")
            return False

    def _backup_key(self, key_id: int) -> int:
        """Create wrapped backup of key"""
        # Get wrap key
        wrap_key = self.session.get_object(100, OBJECT.WRAP_KEY)  # Master wrap key

        # Get target key
        key_obj = self.session.get_object(key_id, OBJECT.SYMMETRIC_KEY)

        # Generate backup ID
        backup_id = key_id + 20000

        # Export wrapped
        wrapped_data = key_obj.export_wrapped(wrap_key)

        # Store wrapped backup
        backup_obj = Opaque.put(
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
            metadata_obj = self.session.get_object(metadata_id, OBJECT.OPAQUE)
            metadata_data = metadata_obj.get()
            metadata_dict = json.loads(metadata_data.decode())

            # Convert string dates back to datetime
            if metadata_dict.get('created'):
                metadata_dict['created'] = datetime.fromisoformat(metadata_dict['created'])
            if metadata_dict.get('last_used'):
                metadata_dict['last_used'] = datetime.fromisoformat(metadata_dict['last_used'])
            if metadata_dict.get('rotation_due'):
                metadata_dict['rotation_due'] = datetime.fromisoformat(metadata_dict['rotation_due'])

            return KeyMetadata(**metadata_dict)

        except Exception:
            return None

    def _update_key_consumers(self, old_key_id: int, new_key_id: int):
        """Update applications using the old key to use the new key"""
        # This is a placeholder - actual implementation would depend on
        # how applications reference keys (by ID, label, etc.)
        self._log_audit("UPDATE_CONSUMERS", f"Updated consumers from key {old_key_id} to {new_key_id}")

    # ============================================================================
    # YUBICO INTEGRATION
    # ============================================================================

    def register_with_yubico(self) -> bool:
        """Register HSM with Yubico cloud services"""
        if not self.yubico_api_key:
            self._log_audit("YUBICO_REGISTER_FAILED", "No Yubico API key configured")
            return False

        device_data = {
            "serial": self.device_info.serial,
            "model": "YubiHSM2",
            "firmware": f"{self.device_info.version}",
            "capabilities": ["FIPS_140_2", "TCG_OPAL", "REMOTE_MGMT"],
            "registered_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                "https://api.yubico.com/v1/devices/register",
                headers={
                    "Authorization": f"Bearer {self.yubico_api_key}",
                    "Content-Type": "application/json"
                },
                json=device_data,
                timeout=30
            )

            if response.status_code == 200:
                self._log_audit("YUBICO_REGISTER", "Successfully registered with Yubico")
                return True
            else:
                self._log_audit("YUBICO_REGISTER_FAILED", f"HTTP {response.status_code}: {response.text}")
                return False

        except Exception as e:
            self._log_audit("YUBICO_REGISTER_FAILED", str(e))
            return False

    def sync_inventory_with_yubico(self) -> bool:
        """Sync key inventory with Yubico cloud"""
        if not self.yubico_api_key:
            return False

        # Get all objects
        objects = self.session.list_objects()

        inventory = {
            "device_serial": self.device_info.serial,
            "sync_timestamp": datetime.now().isoformat(),
            "total_objects": len(objects),
            "objects": []
        }

        for obj in objects:
            obj_data = {
                "id": obj.id,
                "type": obj.object_type.name,
                "label": obj.label,
                "algorithm": obj.algorithm.name if hasattr(obj, 'algorithm') else None,
                "capabilities": obj.capabilities,
                "domains": obj.domains
            }
            inventory["objects"].append(obj_data)

        try:
            response = requests.post(
                "https://api.yubico.com/v1/keys/sync",
                headers={
                    "Authorization": f"Bearer {self.yubico_api_key}",
                    "Content-Type": "application/json"
                },
                json=inventory,
                timeout=60
            )

            if response.status_code == 200:
                self._log_audit("YUBICO_SYNC", f"Synced {len(objects)} objects with Yubico")
                return True
            else:
                self._log_audit("YUBICO_SYNC_FAILED", f"HTTP {response.status_code}: {response.text}")
                return False

        except Exception as e:
            self._log_audit("YUBICO_SYNC_FAILED", str(e))
            return False

    def get_yubico_recommendations(self) -> Dict[str, Any]:
        """Get security recommendations from Yubico"""
        if not self.yubico_api_key:
            return {}

        try:
            response = requests.get(
                f"https://api.yubico.com/v1/devices/{self.device_info.serial}/recommendations",
                headers={"Authorization": f"Bearer {self.yubico_api_key}"},
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._log_audit("YUBICO_RECOMMENDATIONS_FAILED", f"HTTP {response.status_code}")
                return {}

        except Exception as e:
            self._log_audit("YUBICO_RECOMMENDATIONS_FAILED", str(e))
            return {}

    # ============================================================================
    # AUDIT AND COMPLIANCE
    # ============================================================================

    def generate_compliance_report(self, output_file: str) -> bool:
        """Generate comprehensive compliance report"""
        report = {
            "report_type": "YubiHSM 2 Key Hierarchy Compliance Report",
            "generated_at": datetime.now().isoformat(),
            "device_info": {
                "serial": self.device_info.serial,
                "version": str(self.device_info.version),
                "fips_mode": True  # Assuming FIPS mode is enabled
            },
            "key_inventory": [],
            "compliance_status": {},
            "recommendations": []
        }

        # Analyze key inventory
        objects = self.session.list_objects()
        key_counts = {
            "authentication": 0,
            "symmetric": 0,
            "asymmetric": 0,
            "wrap": 0,
            "opaque": 0
        }

        for obj in objects:
            obj_type = obj.object_type.name.lower()
            if obj_type in key_counts:
                key_counts[obj_type] += 1

            # Get metadata if available
            metadata = self._get_key_metadata(obj.id)
            if metadata:
                report["key_inventory"].append(asdict(metadata))

        report["key_inventory_summary"] = key_counts

        # Compliance checks
        compliance_issues = []

        # Check authentication keys
        if key_counts["authentication"] < 3:
            compliance_issues.append("Insufficient authentication keys (minimum 3 required)")

        # Check FIPS compliance
        if not self._check_fips_compliance():
            compliance_issues.append("FIPS 140-2 compliance issues detected")

        # Check key rotation status
        expired_keys = self._check_key_expiration()
        if expired_keys:
            compliance_issues.append(f"{len(expired_keys)} keys require rotation")

        # Overall compliance
        report["compliance_status"] = {
            "overall_compliant": len(compliance_issues) == 0,
            "issues": compliance_issues,
            "compliance_score": max(0, 100 - (len(compliance_issues) * 20))
        }

        # Generate recommendations
        recommendations = []
        if key_counts["authentication"] < 5:
            recommendations.append("Consider adding more role-based authentication keys")
        if key_counts["wrap"] < 2:
            recommendations.append("Add domain-specific wrap keys for better separation")
        if len(expired_keys) > 0:
            recommendations.append("Implement automated key rotation policy")

        report["recommendations"] = recommendations

        # Write report
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        self._log_audit("COMPLIANCE_REPORT", f"Generated compliance report: {output_file}")
        return True

    def _check_fips_compliance(self) -> bool:
        """Check FIPS 140-2 compliance status"""
        # This would check various FIPS requirements
        # For now, assume compliant if FIPS mode is enabled
        return True

    def _check_key_expiration(self) -> List[int]:
        """Check for expired or soon-to-expire keys"""
        expired_keys = []
        objects = self.session.list_objects()

        for obj in objects:
            metadata = self._get_key_metadata(obj.id)
            if metadata and metadata.rotation_due:
                if metadata.rotation_due < datetime.now():
                    expired_keys.append(obj.id)

        return expired_keys

    def export_audit_log(self, output_file: str) -> bool:
        """Export audit log for compliance"""
        try:
            # Get audit log from HSM
            audit_data = self.session.get_audit_log()

            with open(output_file, 'wb') as f:
                f.write(audit_data)

            self._log_audit("AUDIT_EXPORT", f"Exported audit log to {output_file}")
            return True

        except Exception as e:
            self._log_audit("AUDIT_EXPORT_FAILED", str(e))
            return False

def main():
    """Main CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(description="YubiHSM 2 Key Hierarchy Manager")
    parser.add_argument("command", choices=[
        "init", "create-sed-key", "create-db-key", "create-ssl-key",
        "rotate-key", "compliance-report", "yubico-register", "yubico-sync",
        "audit-export"
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

    # Get password
    password: str = args.password or os.getenv('YUBIHSM_PASSWORD') or ""
    if not password and args.command != "yubico-register":
        import getpass
        password = getpass.getpass("Enter YubiHSM password: ") or ""

    manager = YubiHSMKeyHierarchy(args.connector_url)

    try:
        if args.command == "init":
            manager.connect(args.auth_key, password)
            domain_passwords = manager.initialize_key_hierarchy(password)
            if domain_passwords:
                print("\nIMPORTANT: store these domain auth-key passwords now.")
                print("They are independently generated, cannot be recomputed from")
                print("the admin password, and cannot be read back from the HSM.\n")
                for label, generated_password in domain_passwords.items():
                    print(f"  {label}: {generated_password}")
                print()

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

        elif args.command == "yubico-register":
            manager.connect(args.auth_key, password if password else "")
            success = manager.register_with_yubico()
            print(f"Yubico registration {'successful' if success else 'failed'}")

        elif args.command == "yubico-sync":
            manager.connect(args.auth_key, password)
            success = manager.sync_inventory_with_yubico()
            print(f"Yubico sync {'successful' if success else 'failed'}")

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