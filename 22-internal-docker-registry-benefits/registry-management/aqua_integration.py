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
# Aqua Security Integration Client
# =============================================================================
"""
Enterprise integration with Aqua Security platform.

Features:
- Image scanning with Aqua Scanner
- Policy enforcement
- Runtime protection configuration
- Compliance reporting
- Integration with enterprise SIEM

Alternative Free Options:
- For budget-conscious deployments, use RegistrySecurityScanner
  with Trivy or Grype backends instead

Regulatory Compliance:
- PCI-DSS: Comprehensive vulnerability management
- HIPAA: Healthcare data protection
- SOX: Financial data integrity
- GDPR: Data privacy enforcement
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class AquaScanResult:
    """Aqua Security scan result."""

    scan_id: str
    image: str
    status: str
    timestamp: datetime
    vulnerabilities_summary: Dict[str, int]
    malware_detected: bool
    sensitive_data_found: bool
    risk_score: int
    risk_level: str
    policy_violations: List[Dict[str, Any]]


@dataclass
class PolicyEnforcementAction:
    """Policy enforcement action."""

    action: str  # BLOCK, QUARANTINE, WARN, ALLOW
    reason: str
    notification_target: str
    details: Dict[str, Any]


class AquaSecurityClient:
    """
    Enterprise integration with Aqua Security platform.

    For enterprise deployments requiring:
    - Advanced runtime protection
    - Kubernetes-native security
    - Comprehensive compliance reporting
    - Integration with enterprise security platforms

    For cost-effective alternatives, consider using
    RegistrySecurityScanner with Trivy (free).

    Example:
        client = AquaSecurityClient(
            server_url='https://aqua-server:443',
            username='admin',
            password='secure_password'
        )

        await client.authenticate()
        result = await client.scan_image('registry.local', 'myapp', 'latest')
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
    ):
        """
        Initialize Aqua Security client.

        Args:
            server_url: Aqua server URL
            username: API username
            password: API password
            verify_ssl: Verify SSL certificates
        """
        self.server_url = server_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._session: Optional[Any] = None
        self.token: Optional[str] = None

    async def _get_session(self) -> Any:
        """Get or create aiohttp session."""
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp required for Aqua Security integration")

        if self._session is None:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
            self._session = aiohttp.ClientSession(connector=connector)

        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def authenticate(self) -> bool:
        """
        Authenticate with Aqua Security server.

        Returns:
            True if authentication successful
        """
        try:
            session = await self._get_session()

            auth_data = {"id": self.username, "password": self.password}

            async with session.post(
                f"{self.server_url}/api/v1/login", json=auth_data
            ) as response:
                if response.status == 200:
                    auth_response = await response.json()
                    self.token = auth_response.get("token")

                    # Update session headers
                    if self._session is not None:
                        self._session.headers.update(
                            {"Authorization": f"Bearer {self.token}"}
                        )

                    logger.info("Aqua Security authentication successful")
                    return True
                else:
                    logger.error(f"Aqua authentication failed: HTTP {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Aqua authentication error: {e}")
            return False

    async def scan_image(
        self, registry: str, image_name: str, tag: str
    ) -> AquaScanResult:
        """
        Scan container image with Aqua Security.

        Args:
            registry: Registry hostname
            image_name: Image name
            tag: Image tag

        Returns:
            AquaScanResult with scan details
        """
        try:
            session = await self._get_session()

            scan_request = {
                "registry": registry,
                "image": image_name,
                "tag": tag,
                "scan_type": "base",
                "scan_options": {
                    "scan_malware": True,
                    "scan_sensitive_data": True,
                    "scan_files": True,
                    "save_adhoc_scan": True,
                },
            }

            async with session.post(
                f"{self.server_url}/api/v2/scans", json=scan_request
            ) as response:
                if response.status == 200:
                    scan_response = await response.json()
                    scan_id = scan_response.get("scan_id")

                    # Wait for scan completion
                    return await self._wait_for_scan_completion(scan_id)
                else:
                    error_text = await response.text()
                    logger.error(f"Scan request failed: HTTP {response.status}")
                    return AquaScanResult(
                        scan_id="",
                        image=f"{registry}/{image_name}:{tag}",
                        status="failed",
                        timestamp=datetime.now(),
                        vulnerabilities_summary={},
                        malware_detected=False,
                        sensitive_data_found=False,
                        risk_score=0,
                        risk_level="UNKNOWN",
                        policy_violations=[],
                    )

        except Exception as e:
            logger.error(f"Image scan failed: {e}")
            return AquaScanResult(
                scan_id="",
                image=f"{registry}/{image_name}:{tag}",
                status="error",
                timestamp=datetime.now(),
                vulnerabilities_summary={},
                malware_detected=False,
                sensitive_data_found=False,
                risk_score=0,
                risk_level="UNKNOWN",
                policy_violations=[],
            )

    async def _wait_for_scan_completion(
        self, scan_id: str, timeout: int = 300
    ) -> AquaScanResult:
        """Wait for scan completion and get results."""
        start_time = datetime.now()
        session = await self._get_session()

        while (datetime.now() - start_time).seconds < timeout:
            try:
                async with session.get(
                    f"{self.server_url}/api/v2/scans/{scan_id}"
                ) as response:
                    if response.status == 200:
                        scan_data = await response.json()
                        status = scan_data.get("status")

                        if status == "completed":
                            return self._process_scan_results(scan_data)
                        elif status == "failed":
                            return AquaScanResult(
                                scan_id=scan_id,
                                image=scan_data.get("image", ""),
                                status="failed",
                                timestamp=datetime.now(),
                                vulnerabilities_summary={},
                                malware_detected=False,
                                sensitive_data_found=False,
                                risk_score=0,
                                risk_level="UNKNOWN",
                                policy_violations=[],
                            )

                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"Scan status check failed: {e}")
                await asyncio.sleep(5)

        return AquaScanResult(
            scan_id=scan_id,
            image="",
            status="timeout",
            timestamp=datetime.now(),
            vulnerabilities_summary={},
            malware_detected=False,
            sensitive_data_found=False,
            risk_score=0,
            risk_level="UNKNOWN",
            policy_violations=[],
        )

    def _process_scan_results(self, scan_data: Dict) -> AquaScanResult:
        """Process and format scan results."""
        vulnerabilities = scan_data.get("vulnerabilities", [])

        vuln_summary = {
            "total": len(vulnerabilities),
            "critical": len([v for v in vulnerabilities if v.get("severity") == "critical"]),
            "high": len([v for v in vulnerabilities if v.get("severity") == "high"]),
            "medium": len([v for v in vulnerabilities if v.get("severity") == "medium"]),
            "low": len([v for v in vulnerabilities if v.get("severity") == "low"]),
        }

        malware = scan_data.get("malware", [])
        sensitive_data = scan_data.get("sensitive_data", [])

        risk_score, risk_level = self._calculate_aqua_risk_score(
            vuln_summary, len(malware) > 0, len(sensitive_data) > 0
        )

        return AquaScanResult(
            scan_id=scan_data.get("id", ""),
            image=scan_data.get("image", ""),
            status="completed",
            timestamp=datetime.now(),
            vulnerabilities_summary=vuln_summary,
            malware_detected=len(malware) > 0,
            sensitive_data_found=len(sensitive_data) > 0,
            risk_score=risk_score,
            risk_level=risk_level,
            policy_violations=scan_data.get("policy_violations", []),
        )

    def _calculate_aqua_risk_score(
        self,
        vuln_summary: Dict[str, int],
        malware_detected: bool,
        sensitive_data_found: bool,
    ) -> tuple:
        """Calculate risk score from Aqua scan results."""
        risk_score = 0

        # Vulnerability risk
        risk_score += vuln_summary.get("critical", 0) * 15
        risk_score += vuln_summary.get("high", 0) * 8
        risk_score += vuln_summary.get("medium", 0) * 3

        # Malware risk
        if malware_detected:
            risk_score += 50

        # Sensitive data risk
        if sensitive_data_found:
            risk_score += 30

        risk_score = min(risk_score, 100)

        # Determine risk level
        if risk_score >= 70:
            risk_level = "CRITICAL"
        elif risk_score >= 50:
            risk_level = "HIGH"
        elif risk_score >= 30:
            risk_level = "MEDIUM"
        elif risk_score >= 10:
            risk_level = "LOW"
        else:
            risk_level = "VERY_LOW"

        return risk_score, risk_level

    async def get_compliance_report(self, image_name: str) -> Dict[str, Any]:
        """
        Get compliance report for image.

        Args:
            image_name: Image name to check

        Returns:
            Compliance report with framework checks
        """
        try:
            session = await self._get_session()

            async with session.get(
                f"{self.server_url}/api/v2/images/{image_name}/compliance"
            ) as response:
                if response.status == 200:
                    return {
                        "status": "success",
                        "compliance": await response.json(),
                    }
                else:
                    return {
                        "status": "failed",
                        "error": f"HTTP {response.status}",
                    }

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def enforce_policies(
        self, scan_result: AquaScanResult
    ) -> PolicyEnforcementAction:
        """
        Enforce security policies based on scan results.

        Args:
            scan_result: Scan result to evaluate

        Returns:
            PolicyEnforcementAction with action details
        """
        enforcement_rules = {
            "CRITICAL": PolicyEnforcementAction(
                action="BLOCK",
                reason="Critical security vulnerabilities detected",
                notification_target="security_team",
                details={
                    "block_deployment": True,
                    "quarantine_existing": True,
                    "alert_priority": "P1",
                },
            ),
            "HIGH": PolicyEnforcementAction(
                action="QUARANTINE",
                reason="High-risk vulnerabilities require review",
                notification_target="security_team",
                details={
                    "block_deployment": True,
                    "quarantine_existing": False,
                    "alert_priority": "P2",
                },
            ),
            "MEDIUM": PolicyEnforcementAction(
                action="WARN",
                reason="Medium-risk issues should be addressed",
                notification_target="development_team",
                details={
                    "block_deployment": False,
                    "alert_priority": "P3",
                },
            ),
            "LOW": PolicyEnforcementAction(
                action="ALLOW",
                reason="Low-risk issues acceptable for deployment",
                notification_target="none",
                details={
                    "block_deployment": False,
                    "alert_priority": "P4",
                },
            ),
            "VERY_LOW": PolicyEnforcementAction(
                action="ALLOW",
                reason="Image meets security standards",
                notification_target="none",
                details={
                    "block_deployment": False,
                    "alert_priority": None,
                },
            ),
        }

        action = enforcement_rules.get(
            scan_result.risk_level, enforcement_rules["LOW"]
        )

        # Execute enforcement action
        await self._execute_enforcement_action(action, scan_result)

        return action

    async def _execute_enforcement_action(
        self, action: PolicyEnforcementAction, scan_result: AquaScanResult
    ) -> None:
        """Execute the enforcement action."""
        if action.action == "BLOCK":
            logger.warning(
                f"BLOCKED: {scan_result.image} - {action.reason}"
            )
            # In production: update image status in registry
            # Block future pulls/deployments

        elif action.action == "QUARANTINE":
            logger.warning(
                f"QUARANTINED: {scan_result.image} - {action.reason}"
            )
            # In production: move to quarantine namespace

        elif action.action == "WARN":
            logger.info(
                f"WARNING: {scan_result.image} - {action.reason}"
            )

        # Send notification if configured
        if action.notification_target != "none":
            await self._send_notification(action, scan_result)

    async def _send_notification(
        self, action: PolicyEnforcementAction, scan_result: AquaScanResult
    ) -> None:
        """Send notification about enforcement action."""
        # Placeholder for notification logic
        # Would integrate with Slack, PagerDuty, email, etc.
        logger.info(
            f"Notification: {action.action} for {scan_result.image} "
            f"-> {action.notification_target}"
        )

    async def configure_runtime_policy(
        self, policy_name: str, policy_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Configure runtime protection policy.

        Args:
            policy_name: Name for the policy
            policy_config: Policy configuration

        Returns:
            Created policy details
        """
        try:
            session = await self._get_session()

            policy_data = {
                "name": policy_name,
                "type": "runtime",
                "enabled": True,
                **policy_config,
            }

            async with session.post(
                f"{self.server_url}/api/v2/policies", json=policy_data
            ) as response:
                if response.status in (200, 201):
                    return {
                        "status": "success",
                        "policy": await response.json(),
                    }
                else:
                    return {
                        "status": "failed",
                        "error": f"HTTP {response.status}",
                    }

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_runtime_events(
        self, image_name: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get runtime security events.

        Args:
            image_name: Filter by image name
            limit: Maximum events to return

        Returns:
            List of runtime events
        """
        try:
            session = await self._get_session()

            params: dict[str, int | str] = {"limit": limit}
            if image_name:
                params["image"] = image_name

            async with session.get(
                f"{self.server_url}/api/v2/events", params=params
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return []

        except Exception as e:
            logger.error(f"Failed to get runtime events: {e}")
            return []


class MockAquaSecurityClient:
    """
    Mock Aqua Security client for testing and development.

    Use this when:
    - Aqua Security is not available
    - Running in development environment
    - Testing CI/CD pipelines
    """

    def __init__(self, *args: Any, **kwargs: Any):
        """Initialize mock client."""
        logger.info("Using MockAquaSecurityClient - for testing only")

    async def authenticate(self) -> bool:
        """Mock authentication."""
        return True

    async def scan_image(
        self, registry: str, image_name: str, tag: str
    ) -> AquaScanResult:
        """Return mock scan result."""
        return AquaScanResult(
            scan_id="mock-scan-001",
            image=f"{registry}/{image_name}:{tag}",
            status="completed",
            timestamp=datetime.now(),
            vulnerabilities_summary={
                "total": 5,
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 2,
            },
            malware_detected=False,
            sensitive_data_found=False,
            risk_score=15,
            risk_level="LOW",
            policy_violations=[],
        )

    async def close(self) -> None:
        """Mock close."""
        pass


def create_aqua_client(
    server_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    use_mock: bool = False,
) -> AquaSecurityClient:
    """
    Factory function to create Aqua client.

    If credentials not provided or use_mock=True, returns mock client.
    """
    if use_mock or not all([server_url, username, password]):
        return MockAquaSecurityClient()  # type: ignore

    return AquaSecurityClient(server_url, username, password)  # type: ignore
