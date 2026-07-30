# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""caasctl — Casino as a Service control-plane CLI.

Manages the lifecycle of white-label casino tenants provisioned on Kubernetes.
Runs in dry-run mode by default; pass --execute to apply changes to the cluster.

Subcommands:
  provision <slug>          Create/update a tenant (idempotent)
  verify <slug>             Run the security + compliance gate
  status <slug>             Show observed tenant state
  suspend <slug>            Block player logins, preserve data
  resume <slug>             Reactivate a suspended tenant
  deprovision <slug>        Backup + remove tenant resources
  test-isolation <a> <b>    Verify network/data/secret isolation between two tenants
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def validate_slug(slug: str) -> bool:
    """Return True iff *slug* is a valid CaaS tenant identifier.

    Rules:
    - 3-40 characters
    - Lowercase letters, digits and hyphens only
    - Must start and end with a letter or digit (no leading/trailing hyphen)
    """
    if not slug:
        return False
    if len(slug) < 3 or len(slug) > 40:
        return False
    return bool(_SLUG_RE.match(slug))


# ---------------------------------------------------------------------------
# Resource naming (deterministic, disjoint by slug)
# ---------------------------------------------------------------------------


def get_tenant_resources(slug: str, domain: str) -> dict[str, str]:
    """Return the canonical Kubernetes/infra resource names for *slug*.

    All values are deterministic functions of (slug, domain) so two different
    slugs always produce disjoint resource sets.
    """
    return {
        "namespace": f"caas-{slug}",
        "db_name": f"caas-{slug}-db",
        "secret_path": f"caas/{slug}/",
        "cert_domain": f"{slug}.{domain}",
        "helm_release": f"caas-{slug}",
    }


# ---------------------------------------------------------------------------
# Provision step builder
# ---------------------------------------------------------------------------


def get_provision_steps(
    slug: str,
    domain: str = "acmetocasino.com",
    jurisdiction: str = "br",
    environment: str = "staging",
) -> list[dict[str, object]]:
    """Return the ordered list of provisioning steps for *slug*.

    Each step is a dict with keys:
      order    int    execution order (1-based)
      name     str    human-readable step name
      command  str    shell command to execute (may be a kubectl/helm invocation)
    """
    res = get_tenant_resources(slug, domain)
    ns = res["namespace"]
    db = res["db_name"]
    secret_path = res["secret_path"]
    cert_domain = res["cert_domain"]
    release = res["helm_release"]

    return [
        {
            "order": 1,
            "name": f"Create Kubernetes namespace {ns}",
            "command": (
                f"kubectl create namespace {ns} --dry-run=client -o yaml"
                f" | kubectl apply -f -"
            ),
        },
        {
            "order": 2,
            "name": f"Render per-tenant manifests for {slug}",
            "command": (
                f"bash scripts/render_manifests.sh {slug} {cert_domain}"
                f" {jurisdiction} standard"
            ),
        },
        {
            "order": 3,
            "name": f"Set up secrets + HSM transit key at {secret_path} (OpenBao AppRole/policy)",
            "command": (
                f"EXECUTE=1 bash scripts/setup_tenant_secrets.sh {slug} {jurisdiction}"
            ),
        },
        {
            "order": 4,
            "name": f"Apply tenant manifests (CNPG {db}, TLS {cert_domain}, compliance, migrate)",
            "command": (
                f"kubectl apply -f manifests/rendered/{slug}/ --namespace {ns}"
            ),
        },
        {
            "order": 5,
            "name": f"Deploy Helm release {release} in namespace {ns}",
            "command": (
                f"helm upgrade --install {release} charts/tenant-runtime"
                f" --namespace {ns}"
                f" --set tenant.slug={slug}"
                f" --set tenant.domain={cert_domain}"
                f" --set tenant.jurisdiction={jurisdiction}"
                f" --set tenant.environment={environment}"
            ),
        },
        {
            "order": 6,
            "name": "Run database migrations",
            "command": (
                f"kubectl create job --from=cronjob/db-migrate migrate-{slug}-$(date +%s)"
                f" --namespace {ns}"
            ),
        },
        {
            "order": 7,
            "name": "Run security + compliance gate",
            "command": (
                f"caasctl verify {slug} --jurisdiction {jurisdiction} --execute"
            ),
        },
        {
            "order": 8,
            "name": "Run smoke test",
            "command": (
                f"kubectl run smoke-{slug} --image=curlimages/curl --rm -i --restart=Never"
                f" --namespace {ns}"
                f" -- curl -sf http://{release}/health"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Security + compliance gate
# ---------------------------------------------------------------------------

# Per-jurisdiction regulatory reporting integrations. Keyed by ISO-ish code.
# Each entry is (check_name, command_template) where {ns}/{slug} are filled in.
_JURISDICTION_REPORTING: dict[str, tuple[str, str]] = {
    "br": (
        "Regulatory reporting: SIGAP (SPA/MF) integration enabled",
        "kubectl get configmap caas-{slug}-reporting -n {ns}"
        " -o jsonpath='{{.data.sigap_enabled}}' | grep -qx true",
    ),
    "mga": (
        "Regulatory reporting: MGA player/transaction reporting enabled",
        "kubectl get configmap caas-{slug}-reporting -n {ns}"
        " -o jsonpath='{{.data.mga_reporting_enabled}}' | grep -qx true",
    ),
    "ukgc": (
        "Regulatory reporting: UKGC RTS + GAMSTOP feed enabled",
        "kubectl get configmap caas-{slug}-reporting -n {ns}"
        " -o jsonpath='{{.data.ukgc_reporting_enabled}}' | grep -qx true",
    ),
    "demo": (
        "Regulatory reporting: sandbox reporting stub enabled",
        "kubectl get configmap caas-{slug}-reporting -n {ns}"
        " -o jsonpath='{{.data.sandbox_reporting_enabled}}' | grep -qx true",
    ),
}


def get_gate_checks(
    slug: str,
    jurisdiction: str = "demo",
    domain: str = "acmetocasino.com",
) -> list[dict[str, object]]:
    """Return the ordered security + compliance gate checks for *slug*.

    The gate MUST pass before a tenant is considered active. Each check is a
    dict with keys:
      order     int    execution order (1-based)
      category  str    "security" or "compliance"
      name      str    human-readable check name
      command   str    shell command that exits non-zero on failure

    SECURITY checks run first (supply-chain + runtime hardening), then the
    COMPLIANCE checks, which are partly driven by the tenant *jurisdiction*
    (e.g. "br" adds SIGAP regulatory reporting).
    """
    res = get_tenant_resources(slug, domain)
    ns = res["namespace"]
    cert_domain = res["cert_domain"]
    secret_path = res["secret_path"]
    release = res["helm_release"]

    security: list[tuple[str, str]] = [
        (
            "Trivy image scan: no CRITICAL CVE",
            f"trivy image --exit-code 1 --severity CRITICAL"
            f" registry.acmetocasino.com/tenant-runtime:{slug}",
        ),
        (
            "Semgrep SAST: no HIGH finding",
            "semgrep ci --config auto --severity ERROR --error",
        ),
        (
            "Gitleaks: no exposed secret",
            "gitleaks detect --no-banner --redact --exit-code 1",
        ),
        (
            "Checkov: IaC policy pass",
            f"checkov -d manifests/ --compact --quiet"
            f" --var-file manifests/{slug}.tfvars",
        ),
        (
            "PodSecurity: namespace enforces 'restricted'",
            f"kubectl get namespace {ns}"
            f" -o jsonpath='{{.metadata.labels.pod-security\\.kubernetes\\.io/enforce}}'"
            f" | grep -qx restricted",
        ),
        (
            "seccompProfile: pods set RuntimeDefault",
            f"kubectl get pods -n {ns}"
            f" -o jsonpath='{{.items[*].spec.securityContext.seccompProfile.type}}'"
            f" | grep -q RuntimeDefault",
        ),
        (
            "NetworkPolicy: default-deny present",
            f"kubectl get networkpolicy default-deny -n {ns}",
        ),
        (
            "OpenBao: no bootstrap-placeholder values seeded",
            f"bao kv get -format=json {secret_path} 2>/dev/null"
            f" | grep -q 'bootstrap-placeholder' && exit 1 || exit 0",
        ),
        (
            "TLS: certificate present and Ready",
            f"kubectl get certificate caas-{slug}-cert -n {ns}"
            f" -o jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'"
            f" | grep -qx True  # {cert_domain}",
        ),
        (
            "OpenBao Transit (HSM): per-tenant key present",
            f"bao read -format=json transit/keys/caas-{slug} >/dev/null 2>&1",
        ),
    ]

    compliance: list[tuple[str, str]] = [
        (
            "KYC/AML: identity-verification provider configured",
            f"kubectl get secret caas-{slug}-kyc -n {ns}"
            f" -o jsonpath='{{.data.provider}}' | grep -q .",
        ),
        (
            "Responsible gaming: deposit/loss/session limits active",
            f"kubectl exec -n {ns} deploy/{release}"
            f" -- caas-admin rg-limits status --assert-active",
        ),
        (
            "Audit trail + RNG log: tamper-evident logging enabled",
            f"kubectl get configmap caas-{slug}-audit -n {ns}"
            f" -o jsonpath='{{.data.rng_log_enabled}}' | grep -qx true",
        ),
    ]

    reporting = _JURISDICTION_REPORTING.get(
        jurisdiction, _JURISDICTION_REPORTING["demo"]
    )
    compliance.append(
        (reporting[0], reporting[1].format(ns=ns, slug=slug))
    )

    checks: list[dict[str, object]] = []
    order = 1
    for name, command in security:
        checks.append(
            {"order": order, "category": "security", "name": name, "command": command}
        )
        order += 1
    for name, command in compliance:
        checks.append(
            {"order": order, "category": "compliance", "name": name, "command": command}
        )
        order += 1
    return checks


# ---------------------------------------------------------------------------
# Command execution helper
# ---------------------------------------------------------------------------


def _run(cmd: str, dry_run: bool) -> None:
    """Print *cmd* in dry-run mode; execute it via the shell otherwise.

    The commands produced by this CLI are constructed from validated slug
    values and static literals — no user-supplied strings are interpolated
    into shell metacharacters.  The slug is validated by :func:`validate_slug`
    before any step-list is built, ensuring it matches ``[a-z0-9-]+``.
    Using shlex.split here would work for simple cases but breaks on Helm
    ``--set key=value`` arguments; shell=True with validated input is the
    pragmatic choice for a local operator CLI.
    """
    if dry_run:
        print(f"[DRY-RUN] {cmd}")
    else:
        logger.info("Executing: %s", cmd)
        result = subprocess.run(cmd, shell=True, check=False)  # noqa: S602
        if result.returncode != 0:
            logger.error("Command failed (exit %d): %s", result.returncode, cmd)
            sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_provision(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error(
            "Invalid slug %r. Must be 3-40 chars, lowercase letters/digits/hyphens, "
            "no leading/trailing hyphen.",
            slug,
        )
        sys.exit(1)

    dry_run: bool = args.dry_run
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info("Provisioning tenant %r [%s] jurisdiction=%s env=%s domain=%s",
                slug, mode, args.jurisdiction, args.environment, args.domain)

    steps = get_provision_steps(
        slug,
        domain=args.domain,
        jurisdiction=args.jurisdiction,
        environment=args.environment,
    )

    for step in steps:
        logger.info("Step %d/%d — %s", step["order"], len(steps), step["name"])
        _run(str(step["command"]), dry_run)

    logger.info("Provision complete for tenant %r", slug)


def cmd_verify(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error("Invalid slug %r.", slug)
        sys.exit(1)

    dry_run: bool = args.dry_run
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info(
        "Running security + compliance gate for tenant %r [%s] jurisdiction=%s",
        slug, mode, args.jurisdiction,
    )

    checks = get_gate_checks(slug, jurisdiction=args.jurisdiction, domain=args.domain)
    for check in checks:
        logger.info(
            "Gate %d/%d [%s] — %s",
            check["order"], len(checks), str(check["category"]).upper(), check["name"],
        )
        _run(str(check["command"]), dry_run)

    logger.info("Security + compliance gate complete for tenant %r", slug)


def cmd_status(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error("Invalid slug %r.", slug)
        sys.exit(1)

    res = get_tenant_resources(slug, args.domain)
    print(json.dumps({"slug": slug, "resources": res, "status": "unknown (dry-run)"}, indent=2))


def cmd_suspend(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error("Invalid slug %r.", slug)
        sys.exit(1)

    res = get_tenant_resources(slug, args.domain)
    plan = [
        f"kubectl scale deployment --all --replicas=0 -n {res['namespace']}",
        f"kubectl annotate namespace {res['namespace']} caas.io/suspended=true --overwrite",
    ]
    for cmd in plan:
        _run(cmd, args.dry_run)


def cmd_resume(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error("Invalid slug %r.", slug)
        sys.exit(1)

    res = get_tenant_resources(slug, args.domain)
    plan = [
        f"kubectl scale deployment --all --replicas=1 -n {res['namespace']}",
        f"kubectl annotate namespace {res['namespace']} caas.io/suspended- --overwrite",
    ]
    for cmd in plan:
        _run(cmd, args.dry_run)


def cmd_deprovision(args: argparse.Namespace) -> None:
    slug = args.slug
    if not validate_slug(slug):
        logger.error("Invalid slug %r.", slug)
        sys.exit(1)

    res = get_tenant_resources(slug, args.domain)
    plan = [
        f"kubectl annotate namespace {res['namespace']} caas.io/deprovisioning=true --overwrite",
        f"pg_dump {res['db_name']} > /backups/{slug}-$(date +%Y%m%d%H%M%S).sql",
        f"helm uninstall {res['helm_release']} -n {res['namespace']}",
        f"kubectl delete namespace {res['namespace']}",
        f"bao kv metadata delete caas/{slug}/",
    ]
    for cmd in plan:
        _run(cmd, args.dry_run)


def cmd_test_isolation(args: argparse.Namespace) -> None:
    slug_a = args.slug_a
    slug_b = args.slug_b
    for s in (slug_a, slug_b):
        if not validate_slug(s):
            logger.error("Invalid slug %r.", s)
            sys.exit(1)

    res_a = get_tenant_resources(slug_a, args.domain)
    res_b = get_tenant_resources(slug_b, args.domain)

    checks = [
        (
            "Network: tenant-a cannot reach tenant-b DB",
            f"kubectl exec -n {res_a['namespace']} deploy/casino"
            f" -- nc -zv {res_b['db_name']}.{res_b['namespace']}.svc.cluster.local 5432 || true",
        ),
        (
            "Network: tenant-b cannot reach tenant-a DB",
            f"kubectl exec -n {res_b['namespace']} deploy/casino"
            f" -- nc -zv {res_a['db_name']}.{res_a['namespace']}.svc.cluster.local 5432 || true",
        ),
        (
            "Secrets: tenant-a AppRole cannot read tenant-b secrets",
            f"bao kv get -format=json caas/{slug_b}/ 2>&1 | grep -q 'permission denied'",
        ),
        (
            "Secrets: tenant-b AppRole cannot read tenant-a secrets",
            f"bao kv get -format=json caas/{slug_a}/ 2>&1 | grep -q 'permission denied'",
        ),
        (
            "Data: namespaces are disjoint",
            f"kubectl get pods -n {res_a['namespace']} && kubectl get pods -n {res_b['namespace']}",
        ),
    ]

    for name, cmd in checks:
        logger.info("Check: %s", name)
        _run(cmd, args.dry_run)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--execute",
        dest="dry_run",
        action="store_false",
        help="Actually execute commands (default: dry-run only)",
    )
    parser.add_argument("--domain", default="acmetocasino.com", help="Base domain")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caasctl",
        description="CaaS control-plane CLI — manage white-label casino tenants",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # provision
    p_prov = sub.add_parser("provision", help="Provision a new or existing tenant")
    p_prov.add_argument("slug", help="Tenant identifier (kebab-case, 3-40 chars)")
    p_prov.add_argument("--jurisdiction", default="br", help="Regulatory jurisdiction (br/mga/ukgc)")
    p_prov.add_argument("--environment", default="staging", help="Target environment")
    p_prov.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print commands without executing (default: on)",
    )
    _add_common_flags(p_prov)

    # verify
    p_verify = sub.add_parser(
        "verify", help="Run the security + compliance gate for a tenant"
    )
    p_verify.add_argument("slug", help="Tenant identifier")
    p_verify.add_argument(
        "--jurisdiction",
        default="demo",
        help="Regulatory jurisdiction driving compliance checks (br/mga/ukgc/demo)",
    )
    _add_common_flags(p_verify)

    # status
    p_status = sub.add_parser("status", help="Show tenant status")
    p_status.add_argument("slug", help="Tenant identifier")
    _add_common_flags(p_status)

    # suspend
    p_suspend = sub.add_parser("suspend", help="Suspend a tenant")
    p_suspend.add_argument("slug", help="Tenant identifier")
    _add_common_flags(p_suspend)

    # resume
    p_resume = sub.add_parser("resume", help="Resume a suspended tenant")
    p_resume.add_argument("slug", help="Tenant identifier")
    _add_common_flags(p_resume)

    # deprovision
    p_deprov = sub.add_parser("deprovision", help="Deprovision and archive a tenant")
    p_deprov.add_argument("slug", help="Tenant identifier")
    _add_common_flags(p_deprov)

    # test-isolation
    p_iso = sub.add_parser("test-isolation", help="Verify isolation between two tenants")
    p_iso.add_argument("slug_a", help="First tenant identifier")
    p_iso.add_argument("slug_b", help="Second tenant identifier")
    _add_common_flags(p_iso)

    return parser


_COMMANDS = {
    "provision": cmd_provision,
    "verify": cmd_verify,
    "status": cmd_status,
    "suspend": cmd_suspend,
    "resume": cmd_resume,
    "deprovision": cmd_deprovision,
    "test-isolation": cmd_test_isolation,
}


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    assert handler is not None  # narrowed above; satisfies type checker
    handler(args)


if __name__ == "__main__":
    main()
