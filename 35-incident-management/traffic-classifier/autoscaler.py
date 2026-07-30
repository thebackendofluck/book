# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Autoscaler - Multi-platform scale-up/scale-down automation.

Supports on-premises (Docker Compose + nginx), AWS (ASG + ECS), and
Cloudflare (Workers KV cache warming + Page Rules).

Scale profiles define multipliers; the autoscaler translates them into
concrete actions on each platform.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp
import asyncssh  # ty:ignore[unresolved-import]
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("autoscaler")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------
class ScaleProfile(str, Enum):
    NORMAL = "normal"
    CAMPAIGN_SMALL = "campaign_small"   # 2x
    CAMPAIGN_LARGE = "campaign_large"   # 5x
    EVENT_MEGA = "event_mega"           # 10x
    ATTACK_LOCKDOWN = "attack_lockdown" # Defensive — minimum footprint


@dataclass
class ScaleSpec:
    name: str
    # On-premises Docker Compose replicas (service_name -> count)
    onprem_replicas: dict[str, int]
    # nginx upstream weight for each backend (host -> weight)
    nginx_upstream_weights: dict[str, int]
    # AWS ASG desired capacity
    aws_asg_desired: int
    # AWS ECS desired count (service_name -> count)
    aws_ecs_desired: dict[str, int]
    # Cloudflare cache TTL for static assets (seconds)
    cf_cache_ttl: int
    # Cloudflare cache-everything paths
    cf_cache_paths: list[str]
    # Grace period before scale-down after campaign ends (seconds)
    scale_down_grace_seconds: int = 600


SCALE_PROFILES: dict[ScaleProfile, ScaleSpec] = {
    ScaleProfile.NORMAL: ScaleSpec(
        name="normal",
        onprem_replicas={"igaming-api": 2, "igaming-frontend": 2, "igaming-worker": 1},
        nginx_upstream_weights={"backend-01": 5, "backend-02": 5},
        aws_asg_desired=2,
        aws_ecs_desired={"igaming-api": 2, "igaming-frontend": 2},
        cf_cache_ttl=300,
        cf_cache_paths=[],
        scale_down_grace_seconds=0,
    ),
    ScaleProfile.CAMPAIGN_SMALL: ScaleSpec(
        name="campaign_small",
        onprem_replicas={"igaming-api": 4, "igaming-frontend": 4, "igaming-worker": 2},
        nginx_upstream_weights={"backend-01": 5, "backend-02": 5},
        aws_asg_desired=4,
        aws_ecs_desired={"igaming-api": 4, "igaming-frontend": 4},
        cf_cache_ttl=3600,
        cf_cache_paths=["/promo/*", "/bonus/*"],
        scale_down_grace_seconds=600,
    ),
    ScaleProfile.CAMPAIGN_LARGE: ScaleSpec(
        name="campaign_large",
        onprem_replicas={"igaming-api": 10, "igaming-frontend": 8, "igaming-worker": 4},
        nginx_upstream_weights={"backend-01": 5, "backend-02": 5, "backend-03": 5},
        aws_asg_desired=10,
        aws_ecs_desired={"igaming-api": 10, "igaming-frontend": 8},
        cf_cache_ttl=7200,
        cf_cache_paths=["/promo/*", "/bonus/*", "/register", "/login"],
        scale_down_grace_seconds=1200,
    ),
    ScaleProfile.EVENT_MEGA: ScaleSpec(
        name="event_mega",
        onprem_replicas={"igaming-api": 20, "igaming-frontend": 16, "igaming-worker": 8},
        nginx_upstream_weights={"backend-01": 5, "backend-02": 5, "backend-03": 5, "backend-04": 5},
        aws_asg_desired=20,
        aws_ecs_desired={"igaming-api": 20, "igaming-frontend": 16},
        cf_cache_ttl=14400,
        cf_cache_paths=["/promo/*", "/bonus/*", "/register", "/login", "/games/*"],
        scale_down_grace_seconds=1800,
    ),
    ScaleProfile.ATTACK_LOCKDOWN: ScaleSpec(
        name="attack_lockdown",
        onprem_replicas={"igaming-api": 2, "igaming-frontend": 2, "igaming-worker": 1},
        nginx_upstream_weights={"backend-01": 5, "backend-02": 5},
        aws_asg_desired=2,
        aws_ecs_desired={"igaming-api": 2, "igaming-frontend": 2},
        cf_cache_ttl=86400,  # Maximise CF cache hit rate during attack
        cf_cache_paths=["/*"],
        scale_down_grace_seconds=0,
    ),
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class AutoscalerConfig:
    # On-premises SSH
    onprem_hosts: list[str] = field(
        default_factory=lambda: os.getenv("ONPREM_HOSTS", "").split(",")
    )
    ssh_user: str = field(default_factory=lambda: os.getenv("SSH_USER", "deploy"))
    ssh_key_path: str = field(
        default_factory=lambda: os.getenv("SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_ed25519"))
    )
    docker_compose_dir: str = field(
        default_factory=lambda: os.getenv("DOCKER_COMPOSE_DIR", "/opt/igaming")
    )
    nginx_upstream_conf: str = field(
        default_factory=lambda: os.getenv("NGINX_UPSTREAM_CONF", "/etc/nginx/upstreams.conf")
    )

    # AWS
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    aws_asg_name: str = field(
        default_factory=lambda: os.getenv("AWS_ASG_NAME", "igaming-asg")
    )
    aws_ecs_cluster: str = field(
        default_factory=lambda: os.getenv("AWS_ECS_CLUSTER", "igaming-cluster")
    )

    # Cloudflare
    cf_api_token: str = field(default_factory=lambda: os.getenv("CF_API_TOKEN", ""))
    cf_zone_id: str = field(default_factory=lambda: os.getenv("CF_ZONE_ID", ""))
    cf_kv_namespace_id: str = field(
        default_factory=lambda: os.getenv("CF_KV_NAMESPACE_ID", "")
    )
    cf_account_id: str = field(
        default_factory=lambda: os.getenv("CF_ACCOUNT_ID", "")
    )

    # K8s (kubectl path or in-cluster)
    k8s_enabled: bool = field(
        default_factory=lambda: os.getenv("K8S_ENABLED", "false").lower() == "true"
    )
    k8s_namespace: str = field(
        default_factory=lambda: os.getenv("K8S_NAMESPACE", "igaming")
    )
    k8s_hpa_name: str = field(
        default_factory=lambda: os.getenv("K8S_HPA_NAME", "igaming-frontend-hpa")
    )
    k8s_hpa_max_replicas: int = field(
        default_factory=lambda: int(os.getenv("K8S_HPA_MAX_REPLICAS", "20"))
    )


@dataclass
class ScaleActionResult:
    platform: str
    action: str
    success: bool
    message: str
    duration_ms: float = 0.0


@dataclass
class ScaleResult:
    profile: str
    direction: str  # "up" | "down"
    actions: list[ScaleActionResult] = field(default_factory=list)
    total_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def succeeded(self) -> list[ScaleActionResult]:
        return [a for a in self.actions if a.success]

    def failed(self) -> list[ScaleActionResult]:
        return [a for a in self.actions if not a.success]

    def summary(self) -> str:
        return (
            f"Scale-{self.direction} [{self.profile}]: "
            f"{len(self.succeeded())} ok / {len(self.failed())} failed "
            f"in {self.total_ms:.0f}ms"
        )


# ---------------------------------------------------------------------------
# Platform adapters
# ---------------------------------------------------------------------------
class OnPremAdapter:
    """SSH-based Docker Compose scaler and nginx upstream updater."""

    def __init__(self, config: AutoscalerConfig) -> None:
        self._cfg = config

    async def _run_ssh(self, host: str, command: str) -> tuple[bool, str]:
        """Execute a remote command and return (success, output)."""
        try:
            async with asyncssh.connect(
                host,
                username=self._cfg.ssh_user,
                client_keys=[self._cfg.ssh_key_path],
                known_hosts=None,  # Use proper known_hosts in production
                connect_timeout=15,
            ) as conn:
                result = await conn.run(command, check=False)
                if result.exit_status == 0:
                    return True, result.stdout.strip()
                return False, result.stderr.strip()
        except Exception as exc:
            return False, str(exc)

    async def scale_services(self, replicas: dict[str, int]) -> list[ScaleActionResult]:
        results: list[ScaleActionResult] = []
        hosts = [h for h in self._cfg.onprem_hosts if h.strip()]
        if not hosts:
            return [ScaleActionResult("onprem", "scale_services", False,
                                      "No on-premises hosts configured.")]

        for host in hosts:
            for service, count in replicas.items():
                t0 = time.monotonic()
                cmd = (
                    f"cd {self._cfg.docker_compose_dir} && "
                    f"docker compose up -d --scale {service}={count} --no-recreate 2>&1"
                )
                success, output = await self._run_ssh(host, cmd)
                duration = (time.monotonic() - t0) * 1000
                results.append(ScaleActionResult(
                    platform=f"onprem:{host}",
                    action=f"scale {service}={count}",
                    success=success,
                    message=output[:200],
                    duration_ms=duration,
                ))
                logger.info("[onprem:%s] scale %s=%d: %s", host, service, count,
                            "ok" if success else "FAILED")

        return results

    async def update_nginx_weights(self, weights: dict[str, int]) -> list[ScaleActionResult]:
        """Rewrite nginx upstream block and reload without downtime."""
        results: list[ScaleActionResult] = []
        hosts = [h for h in self._cfg.onprem_hosts if h.strip()]
        if not hosts:
            return [ScaleActionResult("onprem", "nginx_reload", False,
                                      "No on-premises hosts configured.")]

        # Build upstream block
        upstream_lines = "\n".join(
            f"    server {backend} weight={w};" for backend, w in weights.items()
        )
        new_conf = f"upstream igaming_backend {{\n{upstream_lines}\n}}\n"

        for host in hosts:
            t0 = time.monotonic()
            # Write config and reload
            cmd = (
                f"echo '{new_conf}' | sudo tee {self._cfg.nginx_upstream_conf} > /dev/null "
                f"&& sudo nginx -t 2>&1 && sudo nginx -s reload 2>&1"
            )
            success, output = await self._run_ssh(host, cmd)
            duration = (time.monotonic() - t0) * 1000
            results.append(ScaleActionResult(
                platform=f"onprem:{host}",
                action="nginx_update_weights",
                success=success,
                message=output[:200],
                duration_ms=duration,
            ))

        return results


class AWSAdapter:
    """boto3-based AWS ASG and ECS scaler."""

    def __init__(self, config: AutoscalerConfig) -> None:
        self._cfg = config

    def _asg(self) -> Any:
        return boto3.client("autoscaling", region_name=self._cfg.aws_region)

    def _ecs(self) -> Any:
        return boto3.client("ecs", region_name=self._cfg.aws_region)

    async def scale_asg(self, desired: int) -> ScaleActionResult:
        t0 = time.monotonic()
        if not self._cfg.aws_asg_name:
            return ScaleActionResult("aws", "asg_scale", False, "ASG name not configured.")
        try:
            self._asg().set_desired_capacity(
                AutoScalingGroupName=self._cfg.aws_asg_name,
                DesiredCapacity=desired,
                HonorCooldown=False,
            )
            duration = (time.monotonic() - t0) * 1000
            logger.info("AWS ASG '%s' desired=%d.", self._cfg.aws_asg_name, desired)
            return ScaleActionResult("aws", f"asg_desired={desired}", True,
                                     "ASG updated.", duration_ms=duration)
        except ClientError as exc:
            return ScaleActionResult("aws", "asg_scale", False, str(exc),
                                     duration_ms=(time.monotonic() - t0) * 1000)

    async def scale_ecs_services(self, desired_map: dict[str, int]) -> list[ScaleActionResult]:
        results: list[ScaleActionResult] = []
        if not self._cfg.aws_ecs_cluster:
            return [ScaleActionResult("aws", "ecs_scale", False, "ECS cluster not configured.")]

        ecs = self._ecs()
        for service_name, desired in desired_map.items():
            t0 = time.monotonic()
            try:
                ecs.update_service(
                    cluster=self._cfg.aws_ecs_cluster,
                    service=service_name,
                    desiredCount=desired,
                    forceNewDeployment=False,
                )
                duration = (time.monotonic() - t0) * 1000
                logger.info("ECS service '%s' desired=%d.", service_name, desired)
                results.append(ScaleActionResult(
                    "aws", f"ecs:{service_name}={desired}", True,
                    "ECS service updated.", duration_ms=duration,
                ))
            except ClientError as exc:
                results.append(ScaleActionResult(
                    "aws", f"ecs:{service_name}", False, str(exc),
                    duration_ms=(time.monotonic() - t0) * 1000,
                ))

        return results


class CloudflareAdapter:
    """Cloudflare cache warming via Workers KV and Page Rules."""

    CF_BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, config: AutoscalerConfig) -> None:
        self._cfg = config
        self._headers = {
            "Authorization": f"Bearer {config.cf_api_token}",
            "Content-Type": "application/json",
        }

    async def set_browser_cache_ttl(self, ttl: int) -> ScaleActionResult:
        t0 = time.monotonic()
        if not self._cfg.cf_api_token or not self._cfg.cf_zone_id:
            return ScaleActionResult("cloudflare", "cache_ttl", False,
                                     "CF credentials not configured.")
        url = f"{self.CF_BASE}/zones/{self._cfg.cf_zone_id}/settings/browser_cache_ttl"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    url,
                    headers=self._headers,
                    json={"value": ttl},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    duration = (time.monotonic() - t0) * 1000
                    body = await resp.json()
                    if resp.status == 200 and body.get("success"):
                        return ScaleActionResult("cloudflare", f"cache_ttl={ttl}", True,
                                                 "Browser cache TTL updated.", duration_ms=duration)
                    return ScaleActionResult("cloudflare", "cache_ttl", False,
                                             f"CF API error: {body}", duration_ms=duration)
        except Exception as exc:
            return ScaleActionResult("cloudflare", "cache_ttl", False, str(exc),
                                     duration_ms=(time.monotonic() - t0) * 1000)

    async def purge_cache(self, paths: list[str]) -> ScaleActionResult:
        """Purge specific paths so the next request re-fills the edge cache."""
        t0 = time.monotonic()
        if not self._cfg.cf_api_token or not self._cfg.cf_zone_id or not paths:
            return ScaleActionResult("cloudflare", "purge_cache", False,
                                     "CF credentials or paths not configured.")
        url = f"{self.CF_BASE}/zones/{self._cfg.cf_zone_id}/purge_cache"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers,
                    json={"files": paths[:30]},  # CF allows max 30 per request
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    duration = (time.monotonic() - t0) * 1000
                    body = await resp.json()
                    if resp.status == 200 and body.get("success"):
                        return ScaleActionResult("cloudflare", "purge_cache", True,
                                                 f"Purged {len(paths)} paths.", duration_ms=duration)
                    return ScaleActionResult("cloudflare", "purge_cache", False,
                                             f"CF API error: {body}", duration_ms=duration)
        except Exception as exc:
            return ScaleActionResult("cloudflare", "purge_cache", False, str(exc),
                                     duration_ms=(time.monotonic() - t0) * 1000)

    async def warm_kv_cache(
        self, paths: list[str], origin_url: str
    ) -> list[ScaleActionResult]:
        """Pre-warm Workers KV by fetching paths through the origin."""
        results: list[ScaleActionResult] = []
        if not self._cfg.cf_kv_namespace_id or not self._cfg.cf_account_id:
            return [ScaleActionResult("cloudflare", "kv_warm", False,
                                      "KV namespace or account ID not configured.")]

        async with aiohttp.ClientSession() as session:
            for path in paths[:20]:
                t0 = time.monotonic()
                key = path.lstrip("/").replace("/", "_") or "index"
                kv_url = (
                    f"{self.CF_BASE}/accounts/{self._cfg.cf_account_id}"
                    f"/storage/kv/namespaces/{self._cfg.cf_kv_namespace_id}/values/{key}"
                )
                try:
                    # Fetch content from origin
                    async with session.get(
                        f"{origin_url}{path}",
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as origin_resp:
                        content = await origin_resp.read()

                    # Write to KV
                    async with session.put(
                        kv_url,
                        headers={
                            "Authorization": f"Bearer {self._cfg.cf_api_token}",
                            "Content-Type": "application/octet-stream",
                        },
                        data=content,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as kv_resp:
                        duration = (time.monotonic() - t0) * 1000
                        if kv_resp.status == 200:
                            results.append(ScaleActionResult(
                                "cloudflare", f"kv_warm:{path}", True,
                                f"Warmed {len(content)} bytes.", duration_ms=duration,
                            ))
                        else:
                            results.append(ScaleActionResult(
                                "cloudflare", f"kv_warm:{path}", False,
                                f"KV write failed: {kv_resp.status}", duration_ms=duration,
                            ))
                except Exception as exc:
                    results.append(ScaleActionResult(
                        "cloudflare", f"kv_warm:{path}", False, str(exc),
                        duration_ms=(time.monotonic() - t0) * 1000,
                    ))

        return results


class K8sAdapter:
    """Patch Kubernetes HPA via kubectl (exec-based, no in-cluster SDK dependency)."""

    def __init__(self, config: AutoscalerConfig) -> None:
        self._cfg = config

    async def patch_hpa_max_replicas(self, max_replicas: int) -> ScaleActionResult:
        t0 = time.monotonic()
        if not self._cfg.k8s_enabled:
            return ScaleActionResult("k8s", "hpa_patch", False, "K8s not enabled.")
        patch = json.dumps({"spec": {"maxReplicas": max_replicas}})
        cmd = (
            f"kubectl patch hpa {self._cfg.k8s_hpa_name} "
            f"-n {self._cfg.k8s_namespace} "
            f"--type=merge -p '{patch}'"
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        duration = (time.monotonic() - t0) * 1000
        if proc.returncode == 0:
            logger.info("K8s HPA '%s' maxReplicas set to %d.", self._cfg.k8s_hpa_name, max_replicas)
            return ScaleActionResult("k8s", f"hpa_max={max_replicas}", True,
                                     stdout.decode().strip(), duration_ms=duration)
        return ScaleActionResult("k8s", "hpa_patch", False,
                                 stderr.decode().strip(), duration_ms=duration)


# ---------------------------------------------------------------------------
# Main autoscaler
# ---------------------------------------------------------------------------
class Autoscaler:
    """
    Orchestrates scale-up and scale-down across all platforms.

    Usage:
        scaler = Autoscaler()
        result = await scaler.scale(ScaleProfile.CAMPAIGN_LARGE)
        await scaler.schedule_scale_down(ScaleProfile.CAMPAIGN_LARGE, after_seconds=7200)
    """

    def __init__(self, config: AutoscalerConfig | None = None) -> None:
        self._cfg = config or AutoscalerConfig()
        self._onprem = OnPremAdapter(self._cfg)
        self._aws = AWSAdapter(self._cfg)
        self._cf = CloudflareAdapter(self._cfg)
        self._k8s = K8sAdapter(self._cfg)
        self._current_profile = ScaleProfile.NORMAL
        self._scale_down_task: asyncio.Task | None = None

    async def scale(
        self,
        profile: ScaleProfile,
        origin_url: str = "",
        direction: str = "up",
    ) -> ScaleResult:
        """Apply a scale profile across all platforms concurrently."""
        spec = SCALE_PROFILES[profile]
        t0 = time.monotonic()
        logger.info("Scaling %s to profile '%s'.", direction, profile.value)

        # Build task list — all platforms fire in parallel
        task_groups: list[Any] = [
            self._onprem.scale_services(spec.onprem_replicas),
            self._onprem.update_nginx_weights(spec.nginx_upstream_weights),
            self._aws.scale_asg(spec.aws_asg_desired),
            self._aws.scale_ecs_services(spec.aws_ecs_desired),
            self._cf.set_browser_cache_ttl(spec.cf_cache_ttl),
            self._k8s.patch_hpa_max_replicas(spec.aws_asg_desired),
        ]

        if spec.cf_cache_paths and direction == "up":
            task_groups.append(self._cf.purge_cache(spec.cf_cache_paths))
            if origin_url:
                task_groups.append(self._cf.warm_kv_cache(spec.cf_cache_paths, origin_url))

        raw_results = await asyncio.gather(*task_groups, return_exceptions=True)

        # Flatten nested lists
        flat: list[ScaleActionResult] = []
        for item in raw_results:
            if isinstance(item, Exception):
                flat.append(ScaleActionResult("unknown", "gather", False, str(item)))
            elif isinstance(item, list):
                flat.extend(item)
            elif isinstance(item, ScaleActionResult):
                flat.append(item)

        self._current_profile = profile
        result = ScaleResult(
            profile=profile.value,
            direction=direction,
            actions=flat,
            total_ms=(time.monotonic() - t0) * 1000,
        )
        logger.info(result.summary())
        return result

    async def scale_down_after(
        self, grace_seconds: int | None = None, profile: ScaleProfile | None = None
    ) -> None:
        """
        Schedule automatic scale-down to NORMAL after a grace period.

        If grace_seconds is None, uses the grace period from the current profile.
        """
        spec = SCALE_PROFILES[self._current_profile]
        delay = grace_seconds if grace_seconds is not None else spec.scale_down_grace_seconds
        if delay <= 0:
            return

        if self._scale_down_task and not self._scale_down_task.done():
            self._scale_down_task.cancel()
            logger.info("Previous scale-down task cancelled.")

        target_profile = profile or ScaleProfile.NORMAL

        async def _delayed_scale_down() -> None:
            logger.info(
                "Scale-down to '%s' scheduled in %ds.", target_profile.value, delay
            )
            await asyncio.sleep(delay)
            await self.scale(target_profile, direction="down")

        self._scale_down_task = asyncio.create_task(_delayed_scale_down())

    def cancel_scale_down(self) -> None:
        """Cancel any pending scale-down (e.g. campaign extended)."""
        if self._scale_down_task and not self._scale_down_task.done():
            self._scale_down_task.cancel()
            logger.info("Pending scale-down task cancelled by request.")

    @property
    def current_profile(self) -> ScaleProfile:
        return self._current_profile


# ---------------------------------------------------------------------------
# CLI entry point for manual scaling
# ---------------------------------------------------------------------------
async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Manual autoscaler trigger.")
    parser.add_argument(
        "profile",
        choices=[p.value for p in ScaleProfile],
        help="Scale profile to apply.",
    )
    parser.add_argument(
        "--direction",
        choices=["up", "down"],
        default="up",
    )
    parser.add_argument("--origin-url", default="", help="Origin URL for CF cache warming.")
    args = parser.parse_args()

    scaler = Autoscaler()
    result = await scaler.scale(
        ScaleProfile(args.profile),
        origin_url=args.origin_url,
        direction=args.direction,
    )
    print(result.summary())
    for action in result.actions:
        status = "OK" if action.success else "FAIL"
        print(f"  [{status}] {action.platform} / {action.action}: {action.message}")


if __name__ == "__main__":
    asyncio.run(_main())
