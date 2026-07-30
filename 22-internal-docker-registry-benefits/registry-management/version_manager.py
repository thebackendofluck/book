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
# Registry Version Manager
# =============================================================================
"""
Automated version management for Docker registries.

Features:
- Check for registry updates
- Automated update with rollback capability
- Security update detection
- Compatibility verification
- Backup and restore operations

Regulatory Compliance:
- PCI-DSS: Change management procedures
- SOX: Audit trails for all updates
- ISO 27001: A.12.6 Technical vulnerability management
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class VersionInfo:
    """Registry version information."""

    version: str
    release_date: Optional[datetime]
    security_updates: List[str]
    breaking_changes: List[str]
    is_lts: bool


@dataclass
class UpdateResult:
    """Result of an update operation."""

    success: bool
    previous_version: str
    new_version: str
    timestamp: datetime
    backup_path: Optional[str]
    error: Optional[str]


class RegistryVersionManager:
    """
    Manages registry version updates and maintenance.

    Features:
    - Check for new registry versions
    - Perform automated updates with rollback
    - Verify update compatibility
    - Create pre-update backups
    - Send notifications on updates

    Example:
        manager = RegistryVersionManager(
            registry_url='https://registry.local:5000',
            notification_webhook='https://slack.webhook.url'
        )
        update_info = await manager.check_for_updates()
        if update_info['update_available']:
            result = await manager.perform_automated_update(
                update_info['latest_version']
            )
    """

    def __init__(
        self,
        registry_url: str,
        notification_webhook: Optional[str] = None,
        backup_path: str = "/backups/registry",
        docker_compose_path: str = "/opt/registry",
    ):
        """
        Initialize version manager.

        Args:
            registry_url: Registry API base URL
            notification_webhook: Webhook URL for notifications
            backup_path: Path for backup storage
            docker_compose_path: Path to docker-compose files
        """
        self.registry_url = registry_url.rstrip("/")
        self.notification_webhook = notification_webhook
        self.backup_path = Path(backup_path)
        self.docker_compose_path = Path(docker_compose_path)
        self._session: Optional[Any] = None

    async def _get_session(self) -> Any:
        """Get or create aiohttp session."""
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp required for async operations")
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def check_for_updates(self) -> Dict[str, Any]:
        """
        Check for available registry updates.

        Returns:
            Dictionary with version information and update status
        """
        try:
            current_version = await self._get_current_version()
            latest_version = await self._get_latest_version_from_dockerhub()

            update_available = self._compare_versions(current_version, latest_version)

            security_updates = await self._check_security_updates()
            compatibility = await self._check_compatibility(current_version, latest_version)

            result: Dict[str, Any] = {
                "current_version": current_version,
                "latest_version": latest_version,
                "update_available": update_available,
                "security_updates": security_updates,
                "compatibility_check": compatibility,
                "checked_at": datetime.now().isoformat(),
            }

            if update_available and self.notification_webhook:
                await self._send_update_notification(result)

            return result

        except Exception as e:
            logger.error(f"Version check failed: {e}")
            return {"error": str(e), "checked_at": datetime.now().isoformat()}

    async def _get_current_version(self) -> str:
        """Get current registry version."""
        try:
            process = await asyncio.create_subprocess_shell(
                'docker inspect registry --format "{{.Config.Image}}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                image_name = stdout.decode().strip().strip('"')
                if ":" in image_name:
                    return image_name.split(":")[-1]
                return "latest"

        except Exception as e:
            logger.error(f"Failed to get current version: {e}")

        return "unknown"

    async def _get_latest_version_from_dockerhub(self) -> str:
        """Get latest registry version from Docker Hub."""
        try:
            session = await self._get_session()

            async with session.get(
                "https://hub.docker.com/v2/repositories/library/registry/tags",
                params={"page_size": 50},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    tags = [t["name"] for t in data.get("results", [])]

                    # Filter semantic versions
                    semantic_tags = []
                    for tag in tags:
                        parts = tag.split(".")
                        if len(parts) >= 2 and all(
                            p.isdigit() for p in parts[:2]
                        ):
                            semantic_tags.append(tag)

                    if semantic_tags:
                        # Sort by version
                        semantic_tags.sort(
                            key=lambda x: [
                                int(p) for p in x.split(".") if p.isdigit()
                            ],
                            reverse=True,
                        )
                        return semantic_tags[0]

        except Exception as e:
            logger.error(f"Failed to get latest version: {e}")

        return "unknown"

    def _compare_versions(self, current: str, latest: str) -> bool:
        """Compare version strings to determine if update is available."""
        if current == "unknown" or latest == "unknown":
            return False

        if current == "latest":
            return True  # Always suggest updating from 'latest'

        try:
            current_parts = [int(x) for x in current.split(".") if x.isdigit()]
            latest_parts = [int(x) for x in latest.split(".") if x.isdigit()]

            # Pad shorter version
            while len(current_parts) < len(latest_parts):
                current_parts.append(0)
            while len(latest_parts) < len(current_parts):
                latest_parts.append(0)

            return latest_parts > current_parts

        except (ValueError, AttributeError):
            return False

    async def _check_security_updates(self) -> List[Dict[str, Any]]:
        """Check for security-related updates."""
        # In production, this would check CVE databases
        # and Docker security advisories
        return []

    async def _check_compatibility(
        self, current: str, target: str
    ) -> Dict[str, Any]:
        """Check compatibility between versions."""
        compatibility: Dict[str, Any] = {
            "compatible": True,
            "warnings": [],
            "breaking_changes": [],
        }

        # Check for major version changes
        if current != "unknown" and target != "unknown":
            try:
                current_major = int(current.split(".")[0])
                target_major = int(target.split(".")[0])

                if target_major > current_major:
                    compatibility["warnings"].append(
                        f"Major version upgrade ({current_major} -> {target_major}) "
                        "may include breaking changes"
                    )
            except (ValueError, IndexError):
                pass

        return compatibility

    async def perform_automated_update(self, target_version: str) -> UpdateResult:
        """
        Perform automated registry update.

        Args:
            target_version: Target version to update to

        Returns:
            UpdateResult with operation details
        """
        logger.info(f"Starting automated update to version {target_version}")
        previous_version = await self._get_current_version()

        try:
            # Create backup
            backup_result = await self._create_backup()
            if not backup_result["success"]:
                return UpdateResult(
                    success=False,
                    previous_version=previous_version,
                    new_version=target_version,
                    timestamp=datetime.now(),
                    backup_path=None,
                    error="Backup failed: " + backup_result.get("error", "Unknown"),
                )

            backup_path = backup_result["backup_path"]

            # Stop current registry
            if not await self._stop_registry():
                await self._rollback_update(backup_path)
                return UpdateResult(
                    success=False,
                    previous_version=previous_version,
                    new_version=target_version,
                    timestamp=datetime.now(),
                    backup_path=backup_path,
                    error="Failed to stop registry",
                )

            # Update image
            if not await self._update_registry_image(target_version):
                await self._rollback_update(backup_path)
                return UpdateResult(
                    success=False,
                    previous_version=previous_version,
                    new_version=target_version,
                    timestamp=datetime.now(),
                    backup_path=backup_path,
                    error="Failed to pull new image",
                )

            # Start new registry
            if not await self._start_registry():
                await self._rollback_update(backup_path)
                return UpdateResult(
                    success=False,
                    previous_version=previous_version,
                    new_version=target_version,
                    timestamp=datetime.now(),
                    backup_path=backup_path,
                    error="Failed to start registry",
                )

            # Verify update
            verify_result = await self._verify_update()
            if not verify_result["success"]:
                await self._rollback_update(backup_path)
                return UpdateResult(
                    success=False,
                    previous_version=previous_version,
                    new_version=target_version,
                    timestamp=datetime.now(),
                    backup_path=backup_path,
                    error="Verification failed: " + verify_result.get("error", "Unknown"),
                )

            # Send success notification
            await self._send_update_success_notification(target_version)

            return UpdateResult(
                success=True,
                previous_version=previous_version,
                new_version=target_version,
                timestamp=datetime.now(),
                backup_path=backup_path,
                error=None,
            )

        except Exception as e:
            logger.error(f"Automated update failed: {e}")
            return UpdateResult(
                success=False,
                previous_version=previous_version,
                new_version=target_version,
                timestamp=datetime.now(),
                backup_path=None,
                error=str(e),
            )

    async def _create_backup(self) -> Dict[str, Any]:
        """Create registry backup before update."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = self.backup_path / f"registry_backup_{timestamp}"

            # Create backup directory
            process = await asyncio.create_subprocess_shell(
                f"mkdir -p {backup_dir}"
            )
            await process.wait()

            # Copy registry data
            process = await asyncio.create_subprocess_shell(
                f"cp -r /var/lib/registry {backup_dir}/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info(f"Backup created at {backup_dir}")
                return {"success": True, "backup_path": str(backup_dir)}
            else:
                return {"success": False, "error": stderr.decode()}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _stop_registry(self) -> bool:
        """Stop registry service."""
        try:
            process = await asyncio.create_subprocess_shell(
                f"cd {self.docker_compose_path} && docker-compose stop registry",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            success = process.returncode == 0
            if success:
                logger.info("Registry stopped successfully")
            return success
        except Exception as e:
            logger.error(f"Failed to stop registry: {e}")
            return False

    async def _update_registry_image(self, version: str) -> bool:
        """Update registry Docker image."""
        try:
            process = await asyncio.create_subprocess_shell(
                f"docker pull registry:{version}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            success = process.returncode == 0
            if success:
                logger.info(f"Pulled registry:{version}")
            return success
        except Exception as e:
            logger.error(f"Failed to pull image: {e}")
            return False

    async def _start_registry(self) -> bool:
        """Start registry service."""
        try:
            process = await asyncio.create_subprocess_shell(
                f"cd {self.docker_compose_path} && docker-compose up -d registry",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            success = process.returncode == 0
            if success:
                logger.info("Registry started successfully")
            return success
        except Exception as e:
            logger.error(f"Failed to start registry: {e}")
            return False

    async def _verify_update(self) -> Dict[str, Any]:
        """Verify registry update success."""
        try:
            # Wait for registry to be ready
            logger.info("Waiting for registry to be ready...")
            await asyncio.sleep(30)

            session = await self._get_session()

            async with session.get(
                f"{self.registry_url}/v2/", timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    logger.info("Registry verification successful")
                    return {"success": True}
                else:
                    return {"success": False, "error": f"HTTP {response.status}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _rollback_update(self, backup_path: str) -> bool:
        """Rollback a failed update."""
        try:
            logger.warning(f"Rolling back update from backup: {backup_path}")

            # Stop registry
            await self._stop_registry()

            # Restore backup
            process = await asyncio.create_subprocess_shell(
                f"cp -r {backup_path}/* /var/lib/registry/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            # Start registry
            await self._start_registry()

            logger.info("Rollback completed successfully")
            return True

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False

    async def _send_update_notification(self, update_info: Dict[str, Any]) -> None:
        """Send update available notification."""
        if not self.notification_webhook:
            return

        try:
            session = await self._get_session()

            payload = {
                "text": "Registry Update Available",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Registry Update Available*\n"
                                f"Current: `{update_info['current_version']}`\n"
                                f"Latest: `{update_info['latest_version']}`"
                            ),
                        },
                    }
                ],
            }

            async with session.post(
                self.notification_webhook, json=payload
            ) as response:
                if response.status == 200:
                    logger.info("Update notification sent")

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    async def _send_update_success_notification(self, version: str) -> None:
        """Send update success notification."""
        if not self.notification_webhook:
            return

        try:
            session = await self._get_session()

            payload = {
                "text": f"Registry Updated to {version}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Registry Update Completed*\n"
                                f"New version: `{version}`\n"
                                f"Status: Success"
                            ),
                        },
                    }
                ],
            }

            async with session.post(
                self.notification_webhook, json=payload
            ) as response:
                if response.status == 200:
                    logger.info("Success notification sent")

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def get_version_history(self) -> List[Dict[str, Any]]:
        """Get version update history."""
        history_file = self.backup_path / "version_history.json"

        if history_file.exists():
            try:
                return json.loads(history_file.read_text())
            except json.JSONDecodeError:
                return []

        return []

    def record_version_update(self, update_result: UpdateResult) -> None:
        """Record version update in history."""
        history = self.get_version_history()

        history.append({
            "timestamp": update_result.timestamp.isoformat(),
            "previous_version": update_result.previous_version,
            "new_version": update_result.new_version,
            "success": update_result.success,
            "backup_path": update_result.backup_path,
            "error": update_result.error,
        })

        history_file = self.backup_path / "version_history.json"
        self.backup_path.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(history, indent=2))
