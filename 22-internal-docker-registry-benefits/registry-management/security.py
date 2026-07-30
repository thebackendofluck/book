# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Registry Security Manager
# =============================================================================
"""
Authentication and access control configuration for internal Docker registries.

Provides:
- htpasswd-based authentication configuration
- Role-based access policies (developer, CI, admin)
- TLS/mTLS configuration
- Integration with enterprise identity providers

Security Standards:
- NIST SP 800-190 (Container Security)
- CIS Docker Benchmark
- PCI-DSS Requirements 7 & 8
"""

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccessPolicy:
    """Role-based access policy definition."""

    name: str
    policy_type: str  # 'registry', 'repository', 'image'
    repository_pattern: str  # e.g., '**', 'prod/*', 'dev/**'
    actions: List[str]  # ['pull', 'push', 'delete', '*']
    conditions: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[datetime] = None


@dataclass
class AuthConfig:
    """Authentication configuration for registry."""

    realm: str
    htpasswd_path: str
    tls_certificate: str
    tls_key: str
    storage_root: str
    upload_purging_enabled: bool = True
    upload_purging_age: str = "168h"  # 7 days
    upload_purging_interval: str = "24h"


class RegistrySecurityManager:
    """
    Manages security configuration for internal Docker registries.

    Features:
    - Generate htpasswd authentication files
    - Create role-based access policies
    - Configure TLS/mTLS settings
    - Generate secure registry configuration
    - Audit access patterns

    Example:
        manager = RegistrySecurityManager(
            registry_url='https://registry.local:5000',
            admin_password='secure_password'
        )
        auth_config = manager.generate_auth_config()
        policies = manager.create_access_policies()
    """

    def __init__(
        self,
        registry_url: str,
        admin_password: str,
        config_dir: str = "/etc/docker-registry",
    ):
        """
        Initialize security manager.

        Args:
            registry_url: Registry base URL
            admin_password: Admin user password
            config_dir: Directory for configuration files
        """
        self.registry_url = registry_url
        self.admin_password = admin_password
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def generate_auth_config(self) -> Dict[str, Any]:
        """
        Generate authentication configuration for Docker registry.

        Returns:
            Complete registry configuration dictionary
        """
        # Create htpasswd file
        htpasswd_content = self._generate_htpasswd("admin", self.admin_password)
        htpasswd_path = self.config_dir / "htpasswd"
        htpasswd_path.write_text(htpasswd_content)

        auth_config: Dict[str, Any] = {
            "version": "0.1",
            "auth": {
                "htpasswd": {
                    "realm": "Registry Realm",
                    "path": str(htpasswd_path),
                }
            },
            "http": {
                "addr": "0.0.0.0:5000",
                "host": self.registry_url,
                "tls": {
                    "certificate": str(self.config_dir / "certs" / "registry.crt"),
                    "key": str(self.config_dir / "certs" / "registry.key"),
                },
                "headers": {
                    "X-Content-Type-Options": ["nosniff"],
                    "X-Frame-Options": ["SAMEORIGIN"],
                    "X-XSS-Protection": ["1; mode=block"],
                    "Strict-Transport-Security": [
                        "max-age=31536000; includeSubDomains"
                    ],
                },
            },
            "storage": {
                "filesystem": {"rootdirectory": "/var/lib/registry", "maxthreads": 100},
                "cache": {"blobdescriptor": "inmemory"},
                "maintenance": {
                    "uploadpurging": {
                        "enabled": True,
                        "age": "168h",
                        "interval": "24h",
                        "dryrun": False,
                    }
                },
                "delete": {"enabled": True},
            },
            "health": {
                "storagedriver": {
                    "enabled": True,
                    "interval": "10s",
                    "threshold": 3,
                }
            },
            "log": {
                "level": "info",
                "formatter": "json",
                "fields": {"service": "registry", "environment": "production"},
            },
        }

        return auth_config

    def _generate_htpasswd(self, username: str, password: str) -> str:
        """
        Generate htpasswd entry using bcrypt.

        Args:
            username: Username for authentication
            password: Password to hash

        Returns:
            htpasswd formatted string
        """
        try:
            # Try using htpasswd command
            result = subprocess.run(
                ["htpasswd", "-nbB", username, password],  # ggignore: htpasswd argument, not a hardcoded secret
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to Python bcrypt if available
            try:
                import bcrypt  # ty:ignore[unresolved-import]

                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
                return f"{username}:{hashed.decode()}"
            except ImportError:
                # Basic SHA hash as last resort (not recommended for production)
                logger.warning("Using SHA hash - install bcrypt for better security")
                sha_hash = hashlib.sha256(password.encode()).hexdigest()
                return f"{username}:{{SHA}}{sha_hash}"

    def create_access_policies(self) -> Dict[str, Any]:
        """
        Create role-based access control policies.

        Returns:
            Dictionary containing access policies for different roles
        """
        policies: Dict[str, Any] = {
            "version": "1.0",
            "policies": [
                {
                    "name": "developer-policy",
                    "type": "registry",
                    "repository": "dev/**",
                    "actions": ["pull", "push"],
                    "description": "Developers can pull and push to dev repositories",
                },
                {
                    "name": "developer-readonly-policy",
                    "type": "registry",
                    "repository": "prod/**",
                    "actions": ["pull"],
                    "description": "Developers can only pull from production",
                },
                {
                    "name": "ci-policy",
                    "type": "registry",
                    "repository": "**",
                    "actions": ["pull", "push"],
                    "description": "CI/CD can pull and push to all repositories",
                    "conditions": {
                        "source_ip_range": ["10.0.0.0/8", "172.16.0.0/12"],
                        "required_labels": ["ci-build"],
                    },
                },
                {
                    "name": "scanner-policy",
                    "type": "registry",
                    "repository": "**",
                    "actions": ["pull"],
                    "description": "Security scanners can pull all images",
                    "conditions": {"service_account": "scanner-sa"},
                },
                {
                    "name": "admin-policy",
                    "type": "registry",
                    "repository": "**",
                    "actions": ["*"],
                    "description": "Administrators have full access",
                },
            ],
            "default_policy": {
                "actions": [],
                "description": "Deny all by default",
            },
        }

        return policies

    def create_user(self, username: str, password: str, roles: List[str]) -> bool:
        """
        Create a new registry user.

        Args:
            username: New username
            password: User password
            roles: List of roles to assign

        Returns:
            True if user created successfully
        """
        htpasswd_path = self.config_dir / "htpasswd"

        # Generate new htpasswd entry
        new_entry = self._generate_htpasswd(username, password)

        # Append to htpasswd file
        with open(htpasswd_path, "a") as f:
            f.write(f"\n{new_entry}")

        # Record user roles
        roles_file = self.config_dir / "user_roles.json"
        try:
            if roles_file.exists():
                user_roles = json.loads(roles_file.read_text())
            else:
                user_roles = {}

            user_roles[username] = {
                "roles": roles,
                "created_at": datetime.now().isoformat(),
            }

            roles_file.write_text(json.dumps(user_roles, indent=2))

            logger.info(f"Created user {username} with roles: {roles}")
            return True

        except Exception as e:
            logger.error(f"Failed to create user {username}: {e}")
            return False

    def generate_tls_config(
        self,
        common_name: str = "registry.local",
        organization: str = "iGaming Corp",
        validity_days: int = 365,
    ) -> Dict[str, str]:
        """
        Generate TLS certificate configuration.

        Args:
            common_name: Certificate CN
            organization: Organization name
            validity_days: Certificate validity in days

        Returns:
            Paths to generated certificate and key
        """
        certs_dir = self.config_dir / "certs"
        certs_dir.mkdir(parents=True, exist_ok=True)

        cert_path = certs_dir / "registry.crt"
        key_path = certs_dir / "registry.key"

        # Generate self-signed certificate using openssl
        openssl_cmd = [
            "openssl",
            "req",
            "-newkey",
            "rsa:4096",
            "-nodes",
            "-sha256",
            "-keyout",
            str(key_path),
            "-x509",
            "-days",
            str(validity_days),
            "-out",
            str(cert_path),
            "-subj",
            f"/C=GB/ST=London/L=London/O={organization}/CN={common_name}",
        ]

        try:
            subprocess.run(openssl_cmd, check=True, capture_output=True)
            logger.info(f"Generated TLS certificate for {common_name}")
            return {"certificate": str(cert_path), "key": str(key_path)}
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to generate TLS certificate: {e}")
            raise

    def audit_access(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """
        Generate access audit report.

        Args:
            start_date: Audit start date
            end_date: Audit end date

        Returns:
            List of access events
        """
        # This would integrate with registry access logs
        audit_events: List[Dict[str, Any]] = []

        # Placeholder for audit log parsing
        # In production, this would parse /var/log/registry/access.log

        logger.info(f"Generated audit report from {start_date} to {end_date}")
        return audit_events

    def validate_configuration(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate registry configuration against security best practices.

        Args:
            config: Registry configuration dictionary

        Returns:
            Validation results with recommendations
        """
        validation_results: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "recommendations": [],
        }

        # Check TLS configuration
        if "http" in config and "tls" not in config.get("http", {}):
            validation_results["errors"].append("TLS not configured - required for production")
            validation_results["valid"] = False

        # Check authentication
        if "auth" not in config:
            validation_results["errors"].append("Authentication not configured")
            validation_results["valid"] = False

        # Check storage configuration
        storage = config.get("storage", {})
        if not storage.get("delete", {}).get("enabled", False):
            validation_results["warnings"].append(
                "Image deletion not enabled - may cause storage issues"
            )

        # Check maintenance configuration
        maintenance = storage.get("maintenance", {})
        if not maintenance.get("uploadpurging", {}).get("enabled", False):
            validation_results["recommendations"].append(
                "Enable upload purging to clean incomplete uploads"
            )

        return validation_results


def create_registry_security_manager(
    registry_url: str, admin_password: str
) -> RegistrySecurityManager:
    """
    Factory function to create RegistrySecurityManager.

    Args:
        registry_url: Registry URL
        admin_password: Admin password

    Returns:
        Configured RegistrySecurityManager instance
    """
    return RegistrySecurityManager(registry_url, admin_password)
