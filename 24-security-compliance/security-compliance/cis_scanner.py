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
CIS Docker Security Benchmark Scanner.

Implements automated checks for CIS Docker Benchmark v1.6.0:
- Host configuration auditing
- Daemon configuration checks
- Image security validation
- Container runtime checks
- Security operations verification

Generates compliance reports with remediation guidance.
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ControlSeverity(Enum):
    """CIS control severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ControlStatus(Enum):
    """Control check status."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class ControlCategory(Enum):
    """CIS Docker Benchmark categories."""

    HOST_CONFIG = "1_host_configuration"
    DAEMON_CONFIG = "2_daemon_configuration"
    IMAGES = "4_container_images"
    RUNTIME = "5_container_runtime"
    SECURITY_OPS = "6_security_operations"


@dataclass
class CISControl:
    """CIS Docker Benchmark control definition."""

    control_id: str
    title: str
    description: str
    severity: ControlSeverity
    category: ControlCategory
    automated: bool
    check_command: str
    remediation: str
    impact: str = ""
    references: list[str] = field(default_factory=list)


@dataclass
class ControlResult:
    """Result of a control check."""

    control_id: str
    title: str
    severity: ControlSeverity
    status: ControlStatus
    output: str
    error: str
    remediation: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditResult:
    """Complete audit result."""

    audit_id: str
    timestamp: datetime
    cis_version: str
    docker_version: str
    host_info: dict[str, str]
    controls: dict[str, ControlResult]
    summary: dict[str, Any]


class CISDockerScanner:
    """
    CIS Docker Security Benchmark scanner.

    Implements automated security checks based on CIS Docker
    Benchmark v1.6.0. Provides comprehensive auditing with
    remediation guidance.

    Usage:
        scanner = CISDockerScanner()
        results = await scanner.run_cis_audit()
        print(results["summary"])
    """

    def __init__(self):
        self.cis_controls = self._load_cis_controls()
        self._audit_counter = 0

    def _load_cis_controls(self) -> dict[str, CISControl]:
        """Load CIS Docker Benchmark controls."""
        return {
            # Section 1: Host Configuration
            "1.1.1": CISControl(
                control_id="1.1.1",
                title="Ensure a separate partition for containers has been created",
                description="All Docker containers and their data and metadata is stored under /var/lib/docker directory. By default, /var/lib/docker would be mounted under / or /var partitions based on availability.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.HOST_CONFIG,
                automated=True,
                check_command="mountpoint -q /var/lib/docker && echo 'separate' || echo 'shared'",
                remediation="Create a separate partition for /var/lib/docker mount point. For new installations, create a separate partition for /var/lib/docker mount point. For systems that have already been installed, use the Logical Volume Manager (LVM) to create a new partition.",
                impact="None",
                references=["CIS Docker Benchmark v1.6.0 - 1.1.1"],
            ),
            "1.1.2": CISControl(
                control_id="1.1.2",
                title="Ensure only trusted users are allowed to control Docker daemon",
                description="The Docker daemon currently requires root privileges. A user added to the docker group gives them full root access.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.HOST_CONFIG,
                automated=True,
                check_command="getent group docker | cut -d: -f4",
                remediation="Remove any untrusted users from the docker group. Review the list of users in the docker group and remove any that do not require access.",
                impact="Only authorized users can control the Docker daemon",
                references=["CIS Docker Benchmark v1.6.0 - 1.1.2"],
            ),
            # Section 2: Daemon Configuration
            "2.1": CISControl(
                control_id="2.1",
                title="Run the Docker daemon as a non-root user, if possible",
                description="It is possible to run the Docker daemon as a non-root user (rootless mode) to mitigate potential vulnerabilities in the daemon and the container runtime.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.DAEMON_CONFIG,
                automated=True,
                check_command="ps -ef | grep dockerd | grep -v grep | awk '{print $1}'",
                remediation="Follow Docker's official documentation for rootless mode setup. Note: Not all features are available in rootless mode.",
                impact="Some Docker features may not be available",
                references=["CIS Docker Benchmark v1.6.0 - 2.1"],
            ),
            "2.2": CISControl(
                control_id="2.2",
                title="Ensure network traffic is restricted between containers on the default bridge",
                description="By default, all network traffic is allowed between containers on the same host on the default bridge network.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.DAEMON_CONFIG,
                automated=True,
                check_command="docker network inspect bridge --format '{{.Options}}'",
                remediation="Run the Docker daemon with --icc=false option or set 'icc': false in the daemon.json configuration file.",
                impact="Container to container network traffic needs explicit exposure",
                references=["CIS Docker Benchmark v1.6.0 - 2.2"],
            ),
            "2.3": CISControl(
                control_id="2.3",
                title="Ensure the logging level is set to 'info'",
                description="Setting up an appropriate log level, configures the Docker daemon to log events that you would want to review later.",
                severity=ControlSeverity.LOW,
                category=ControlCategory.DAEMON_CONFIG,
                automated=True,
                check_command="ps -ef | grep dockerd | grep -v grep | grep -o '\\-\\-log-level=[a-z]*' || echo 'info (default)'",
                remediation="Ensure the Docker daemon configuration file has log-level set to info: 'log-level': 'info' in daemon.json",
                impact="None. This is default behavior.",
                references=["CIS Docker Benchmark v1.6.0 - 2.3"],
            ),
            "2.4": CISControl(
                control_id="2.4",
                title="Ensure Docker is allowed to make changes to iptables",
                description="Docker daemon automatically makes required changes to the iptables rules to permit communication between containers.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.DAEMON_CONFIG,
                automated=True,
                check_command="ps -ef | grep dockerd | grep -v grep | grep -c '\\-\\-iptables=false' || echo '0'",
                remediation="Do not use the --iptables=false argument. Let Docker manage iptables.",
                impact="None",
                references=["CIS Docker Benchmark v1.6.0 - 2.4"],
            ),
            # Section 4: Container Images
            "4.1": CISControl(
                control_id="4.1",
                title="Ensure a user for the container has been created",
                description="Containers should run as a non-root user. Running as root in a container is equivalent to running as root on the host.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.IMAGES,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: User={{.Config.User}}'",
                remediation="Ensure Dockerfiles have a USER directive to specify a non-root user. Add USER <username> after installing packages.",
                impact="Applications may require modifications to run as non-root",
                references=["CIS Docker Benchmark v1.6.0 - 4.1"],
            ),
            "4.2": CISControl(
                control_id="4.2",
                title="Ensure that containers use only trusted base images",
                description="Ensure that the container image is written either from scratch or is based on another established and trusted base image.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.IMAGES,
                automated=True,
                check_command="docker images --format '{{.Repository}}:{{.Tag}}'",
                remediation="Use images from official or verified repositories only. Implement image signing and verification with Docker Content Trust.",
                impact="May limit image choices",
                references=["CIS Docker Benchmark v1.6.0 - 4.2"],
            ),
            "4.4": CISControl(
                control_id="4.4",
                title="Ensure images are scanned and rebuilt to include security patches",
                description="Images should be scanned frequently for any vulnerabilities. Vulnerabilities should be patched and images rebuilt.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.IMAGES,
                automated=True,
                check_command="which trivy >/dev/null 2>&1 && echo 'scanner available' || echo 'no scanner'",
                remediation="Use vulnerability scanning tools like Trivy, Grype, or commercial solutions. Integrate scanning into CI/CD pipelines.",
                impact="Additional infrastructure and processes required",
                references=["CIS Docker Benchmark v1.6.0 - 4.4"],
            ),
            "4.6": CISControl(
                control_id="4.6",
                title="Ensure that HEALTHCHECK instructions have been added to container images",
                description="Container images should contain HEALTHCHECK instructions. This ensures that the orchestrator is able to identify unhealthy containers.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.IMAGES,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: {{.Config.Healthcheck}}'",
                remediation="Add HEALTHCHECK instruction to Dockerfiles: HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost/ || exit 1",
                impact="Slightly increased container resource usage",
                references=["CIS Docker Benchmark v1.6.0 - 4.6"],
            ),
            # Section 5: Container Runtime
            "5.1": CISControl(
                control_id="5.1",
                title="Ensure that, if applicable, an AppArmor Profile is enabled",
                description="AppArmor protects the container by restricting what actions it can perform.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: AppArmor={{.AppArmorProfile}}'",
                remediation="Enable AppArmor in the host kernel and apply a suitable profile to containers. Use --security-opt apparmor=<profile>",
                impact="Applications may require profile customization",
                references=["CIS Docker Benchmark v1.6.0 - 5.1"],
            ),
            "5.2": CISControl(
                control_id="5.2",
                title="Ensure that, if applicable, SELinux security options are set",
                description="SELinux is an effective and easy-to-use Linux application security system.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: SecurityOpt={{.HostConfig.SecurityOpt}}'",
                remediation="Enable SELinux in enforcing mode. Use --security-opt label=type:<type> for containers.",
                impact="Requires SELinux policy configuration",
                references=["CIS Docker Benchmark v1.6.0 - 5.2"],
            ),
            "5.4": CISControl(
                control_id="5.4",
                title="Ensure that privileged containers are not used",
                description="Privileged containers have full access to all devices and can perform almost any kernel operation.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: Privileged={{.HostConfig.Privileged}}'",
                remediation="Do not use --privileged flag. If capabilities are needed, use --cap-add to add only required capabilities.",
                impact="Some applications may require reconfiguration",
                references=["CIS Docker Benchmark v1.6.0 - 5.4"],
            ),
            "5.7": CISControl(
                control_id="5.7",
                title="Ensure privileged ports are not mapped within containers",
                description="Do not map container ports to privileged host ports (<1024).",
                severity=ControlSeverity.LOW,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: {{.NetworkSettings.Ports}}'",
                remediation="Start container port mapping from 1024 or higher. Use reverse proxy for ports 80/443.",
                impact="May require reverse proxy configuration",
                references=["CIS Docker Benchmark v1.6.0 - 5.7"],
            ),
            "5.12": CISControl(
                control_id="5.12",
                title="Ensure that the container's root filesystem is mounted as read only",
                description="The container's root filesystem should be treated as a read-only file system.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}}'",
                remediation="Add --read-only flag when running containers. Use volumes for writable data.",
                impact="Applications may require volume mounts for writable areas",
                references=["CIS Docker Benchmark v1.6.0 - 5.12"],
            ),
            "5.25": CISControl(
                control_id="5.25",
                title="Ensure that the container is restricted from acquiring additional privileges",
                description="Restrict the container from acquiring additional privileges via suid or sgid bits.",
                severity=ControlSeverity.HIGH,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: SecurityOpt={{.HostConfig.SecurityOpt}}'",
                remediation="Add --security-opt=no-new-privileges:true when running containers.",
                impact="Containers cannot escalate privileges",
                references=["CIS Docker Benchmark v1.6.0 - 5.25"],
            ),
            "5.28": CISControl(
                control_id="5.28",
                title="Ensure that the PIDs cgroup limit is used",
                description="Limit the number of process IDs (PIDs) a container can use.",
                severity=ControlSeverity.MEDIUM,
                category=ControlCategory.RUNTIME,
                automated=True,
                check_command="docker ps -q | xargs -r docker inspect --format='{{.Id}}: PidsLimit={{.HostConfig.PidsLimit}}'",
                remediation="Use --pids-limit flag to set an appropriate PID limit for containers.",
                impact="Containers with PID-intensive workloads may be limited",
                references=["CIS Docker Benchmark v1.6.0 - 5.28"],
            ),
        }

    async def run_cis_audit(self) -> dict[str, Any]:
        """
        Run comprehensive CIS Docker audit.

        Returns:
            Audit results with summary and remediation guidance
        """
        self._audit_counter += 1
        timestamp = datetime.now(timezone.utc)
        audit_id = f"CIS-AUDIT-{timestamp.strftime('%Y%m%d%H%M%S')}-{self._audit_counter:04d}"

        logger.info("Starting CIS Docker Security Benchmark audit")

        # Get Docker version
        docker_version = await self._get_docker_version()

        # Get host info
        host_info = await self._get_host_info()

        # Run all control checks
        control_results: dict[str, ControlResult] = {}

        for control_id, control in self.cis_controls.items():
            try:
                result = await self._check_control(control)
                control_results[control_id] = result
                logger.info(f"Control {control_id}: {result.status.value}")
            except Exception as e:
                logger.error(f"Failed to check control {control_id}: {e}")
                control_results[control_id] = ControlResult(
                    control_id=control_id,
                    title=control.title,
                    severity=control.severity,
                    status=ControlStatus.ERROR,
                    output="",
                    error=str(e),
                    remediation=control.remediation,
                )

        # Generate summary
        summary = self._generate_audit_summary(control_results)

        return {
            "audit_id": audit_id,
            "timestamp": timestamp.isoformat(),
            "cis_version": "1.6.0",
            "docker_version": docker_version,
            "host_info": host_info,
            "controls": {
                cid: self._result_to_dict(result)
                for cid, result in control_results.items()
            },
            "summary": summary,
            "remediation_priority": self._prioritize_remediation(control_results),
        }

    async def _get_docker_version(self) -> str:
        """Get Docker version."""
        try:
            process = await asyncio.create_subprocess_shell(
                "docker --version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            return stdout.decode().strip()
        except Exception:
            return "unknown"

    async def _get_host_info(self) -> dict[str, str]:
        """Get host system information."""
        info = {}
        try:
            # Kernel version
            process = await asyncio.create_subprocess_shell(
                "uname -r",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            info["kernel"] = stdout.decode().strip()

            # OS info
            process = await asyncio.create_subprocess_shell(
                "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            info["os"] = stdout.decode().strip() or "unknown"

        except Exception as e:
            info["error"] = str(e)

        return info

    async def _check_control(self, control: CISControl) -> ControlResult:
        """Check individual CIS control."""
        try:
            process = await asyncio.create_subprocess_shell(
                control.check_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            output = stdout.decode().strip()
            error = stderr.decode().strip()

            # Determine status based on control and output
            return_code = process.returncode if process.returncode is not None else -1
            status = self._evaluate_control_output(control, output, return_code)

            # Get additional details for specific controls
            details = self._get_control_details(control, output)

            return ControlResult(
                control_id=control.control_id,
                title=control.title,
                severity=control.severity,
                status=status,
                output=output,
                error=error,
                remediation=control.remediation,
                details=details,
            )

        except Exception as e:
            return ControlResult(
                control_id=control.control_id,
                title=control.title,
                severity=control.severity,
                status=ControlStatus.ERROR,
                output="",
                error=str(e),
                remediation=control.remediation,
            )

    def _evaluate_control_output(
        self, control: CISControl, output: str, return_code: int
    ) -> ControlStatus:
        """Evaluate control check output to determine status."""
        control_id = control.control_id

        # Control-specific evaluation logic
        if control_id == "1.1.1":
            return ControlStatus.PASSED if "separate" in output else ControlStatus.FAILED

        elif control_id == "2.1":
            return ControlStatus.PASSED if output != "root" else ControlStatus.WARNING

        elif control_id == "2.2":
            return ControlStatus.FAILED if "true" in output.lower() else ControlStatus.PASSED

        elif control_id == "4.1":
            # Check if any containers run as root (empty user)
            if "User=" in output and "User=]" not in output:
                return ControlStatus.PASSED
            return ControlStatus.WARNING

        elif control_id == "4.4":
            return ControlStatus.PASSED if "scanner available" in output else ControlStatus.WARNING

        elif control_id == "5.4":
            # Check for privileged containers
            if "Privileged=true" in output:
                return ControlStatus.FAILED
            return ControlStatus.PASSED

        elif control_id == "5.12":
            # Check for read-only root filesystem
            if "ReadonlyRootfs=false" in output:
                return ControlStatus.WARNING
            return ControlStatus.PASSED

        elif control_id == "5.25":
            # Check for no-new-privileges
            if "no-new-privileges" in output:
                return ControlStatus.PASSED
            return ControlStatus.WARNING

        # Default: use return code
        return ControlStatus.PASSED if return_code == 0 else ControlStatus.WARNING

    def _get_control_details(
        self, control: CISControl, output: str
    ) -> dict[str, Any]:
        """Get additional details for specific controls."""
        details: dict[str, Any] = {}

        if control.control_id == "4.1":
            # Parse container user info
            containers = []
            for line in output.split("\n"):
                if "User=" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        container_id = parts[0].strip()[:12]
                        user = parts[1].replace("User=", "").strip()
                        containers.append({
                            "container_id": container_id,
                            "user": user or "root (default)",
                        })
            details["containers"] = containers

        elif control.control_id == "5.4":
            # Parse privileged status
            containers = []
            for line in output.split("\n"):
                if "Privileged=" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        container_id = parts[0].strip()[:12]
                        privileged = "true" in parts[1].lower()
                        containers.append({
                            "container_id": container_id,
                            "privileged": privileged,
                        })
            details["containers"] = containers

        return details

    def _generate_audit_summary(
        self, results: dict[str, ControlResult]
    ) -> dict[str, Any]:
        """Generate audit summary statistics."""
        by_status: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_category: dict[str, dict[str, int]] = {}

        for result in results.values():
            # By status
            status = result.status.value
            by_status[status] = by_status.get(status, 0) + 1

            # By severity (for failures/warnings only)
            if result.status in [ControlStatus.FAILED, ControlStatus.WARNING]:
                sev = result.severity.value
                by_severity[sev] = by_severity.get(sev, 0) + 1

            # By category
            control = self.cis_controls.get(result.control_id)
            if control:
                cat = control.category.value
                if cat not in by_category:
                    by_category[cat] = {"passed": 0, "failed": 0, "warning": 0}
                if result.status == ControlStatus.PASSED:
                    by_category[cat]["passed"] += 1
                elif result.status == ControlStatus.FAILED:
                    by_category[cat]["failed"] += 1
                elif result.status == ControlStatus.WARNING:
                    by_category[cat]["warning"] += 1

        total = len(results)
        passed = by_status.get("passed", 0)
        compliance_score = (passed / total * 100) if total > 0 else 0

        return {
            "total_controls": total,
            "by_status": by_status,
            "by_severity": by_severity,
            "by_category": by_category,
            "compliance_score_percent": round(compliance_score, 1),
            "overall_status": "PASS" if compliance_score >= 80 else "NEEDS_ATTENTION",
        }

    def _prioritize_remediation(
        self, results: dict[str, ControlResult]
    ) -> list[dict[str, Any]]:
        """Prioritize remediation actions."""
        priority_list = []

        severity_order = {
            ControlSeverity.CRITICAL: 0,
            ControlSeverity.HIGH: 1,
            ControlSeverity.MEDIUM: 2,
            ControlSeverity.LOW: 3,
            ControlSeverity.INFO: 4,
        }

        for result in results.values():
            if result.status in [ControlStatus.FAILED, ControlStatus.WARNING]:
                priority_list.append({
                    "control_id": result.control_id,
                    "title": result.title,
                    "severity": result.severity.value,
                    "status": result.status.value,
                    "remediation": result.remediation,
                    "priority_score": severity_order.get(result.severity, 99),
                })

        # Sort by severity
        priority_list.sort(key=lambda x: x["priority_score"])

        return priority_list

    def _result_to_dict(self, result: ControlResult) -> dict[str, Any]:
        """Convert ControlResult to dictionary."""
        return {
            "control_id": result.control_id,
            "title": result.title,
            "severity": result.severity.value,
            "status": result.status.value,
            "output": result.output,
            "error": result.error,
            "remediation": result.remediation,
            "details": result.details,
        }

    async def scan_image(self, image_name: str) -> dict[str, Any]:
        """
        Scan a Docker image for CIS compliance.

        Args:
            image_name: Name of the image to scan

        Returns:
            Scan results with recommendations
        """
        checks: list[dict[str, Any]] = []
        recommendations: list[str] = []

        # Check for USER instruction
        user_check = await self._check_image_user(image_name)
        checks.append(user_check)

        # Check for HEALTHCHECK
        health_check = await self._check_image_healthcheck(image_name)
        checks.append(health_check)

        # Generate recommendations
        for check in checks:
            if check.get("status") != "passed":
                remediation = check.get("remediation", "")
                if remediation:
                    recommendations.append(remediation)

        return {
            "image": image_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "recommendations": recommendations,
        }

    async def _check_image_user(self, image_name: str) -> dict[str, Any]:
        """Check if image has non-root user."""
        try:
            process = await asyncio.create_subprocess_shell(
                f"docker inspect {image_name} --format '{{{{.Config.User}}}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            user = stdout.decode().strip()

            return {
                "check": "4.1 - Non-root user",
                "status": "passed" if user and user != "root" else "failed",
                "value": user or "root (default)",
                "remediation": "Add USER directive to Dockerfile specifying a non-root user",
            }
        except Exception as e:
            return {
                "check": "4.1 - Non-root user",
                "status": "error",
                "error": str(e),
                "remediation": "Add USER directive to Dockerfile",
            }

    async def _check_image_healthcheck(self, image_name: str) -> dict[str, Any]:
        """Check if image has HEALTHCHECK."""
        try:
            process = await asyncio.create_subprocess_shell(
                f"docker inspect {image_name} --format '{{{{.Config.Healthcheck}}}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            healthcheck = stdout.decode().strip()

            has_healthcheck = healthcheck and healthcheck != "<nil>"

            return {
                "check": "4.6 - HEALTHCHECK instruction",
                "status": "passed" if has_healthcheck else "warning",
                "value": healthcheck if has_healthcheck else "not configured",
                "remediation": "Add HEALTHCHECK instruction to Dockerfile",
            }
        except Exception as e:
            return {
                "check": "4.6 - HEALTHCHECK instruction",
                "status": "error",
                "error": str(e),
                "remediation": "Add HEALTHCHECK instruction to Dockerfile",
            }
