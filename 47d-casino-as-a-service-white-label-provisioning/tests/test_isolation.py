# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for CaaS tenant isolation — runs offline, no cluster needed."""

import sys
from pathlib import Path

# Add scripts dir to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from caasctl import (  # type: ignore[import-not-found]
    get_gate_checks,
    get_provision_steps,
    get_tenant_resources,
    validate_slug,
)


# ---------------------------------------------------------------------------
# validate_slug
# ---------------------------------------------------------------------------


def test_valid_slugs() -> None:
    for slug in ("demo", "acme-casino", "tenant-123", "abc"):
        assert validate_slug(slug) is True, f"Expected {slug!r} to be valid"


def test_invalid_slugs() -> None:
    invalid = [
        "ab",           # too short (2 chars)
        "A",            # uppercase, also too short
        "-bad",         # leading hyphen
        "bad-",         # trailing hyphen
        "bad slug",     # contains space
        "",             # empty
        "x" * 41,       # too long (41 chars)
    ]
    for slug in invalid:
        assert validate_slug(slug) is False, f"Expected {slug!r} to be invalid"


# ---------------------------------------------------------------------------
# get_provision_steps
# ---------------------------------------------------------------------------


def test_dry_run_produces_ordered_steps() -> None:
    steps = get_provision_steps(
        "demo-tenant",
        domain="acmetocasino.com",
        jurisdiction="br",
        environment="staging",
    )
    assert len(steps) >= 7, f"Expected at least 7 steps, got {len(steps)}"

    for step in steps:
        assert "order" in step, f"Step missing 'order' key: {step}"
        assert "name" in step, f"Step missing 'name' key: {step}"
        assert "command" in step, f"Step missing 'command' key: {step}"

    orders = [step["order"] for step in steps]
    assert orders == sorted(orders), "Steps are not in ascending order"


# ---------------------------------------------------------------------------
# get_tenant_resources — disjoint namespaces
# ---------------------------------------------------------------------------


def test_disjoint_namespaces() -> None:
    res_a = get_tenant_resources("tenant-a", "acmetocasino.com")
    res_b = get_tenant_resources("tenant-b", "acmetocasino.com")

    assert res_a["namespace"] != res_b["namespace"], "Namespaces must differ"
    assert res_a["db_name"] != res_b["db_name"], "DB names must differ"
    assert res_a["secret_path"] != res_b["secret_path"], "Secret paths must differ"


def test_resource_names_contain_slug() -> None:
    res = get_tenant_resources("my-tenant", "acmetocasino.com")
    assert "my-tenant" in res["namespace"], (
        f"Expected 'my-tenant' in namespace, got {res['namespace']!r}"
    )


# ---------------------------------------------------------------------------
# Step commands contain the slug
# ---------------------------------------------------------------------------


def test_step_commands_contain_slug() -> None:
    slug = "demo-tenant"
    steps = get_provision_steps(slug, domain="acmetocasino.com")
    for step in steps:
        cmd = str(step["command"])
        assert slug in cmd, (
            f"Step {step['order']} command does not contain slug {slug!r}: {cmd!r}"
        )


# ---------------------------------------------------------------------------
# Security + compliance gate
# ---------------------------------------------------------------------------


def test_gate_has_security_and_compliance_checks() -> None:
    checks = get_gate_checks("demo-tenant", jurisdiction="demo")
    categories = {str(c["category"]) for c in checks}
    assert "security" in categories, "Gate must include security checks"
    assert "compliance" in categories, "Gate must include compliance checks"

    # ordering: gate checks are 1-based and ascending
    orders = [c["order"] for c in checks]
    assert orders == sorted(orders), "Gate checks are not in ascending order"

    for check in checks:
        assert "order" in check
        assert "category" in check
        assert "name" in check
        assert "command" in check


def test_gate_includes_core_security_controls() -> None:
    checks = get_gate_checks("demo-tenant", jurisdiction="demo")
    blob = " ".join(str(c["name"]) + " " + str(c["command"]) for c in checks)
    for needle in (
        "Trivy",
        "Semgrep",
        "Gitleaks",
        "Checkov",
        "restricted",
        "RuntimeDefault",
        "default-deny",
        "bootstrap-placeholder",
        "TLS",
    ):
        assert needle in blob, f"Expected security control {needle!r} in gate"


def test_gate_includes_core_compliance_controls() -> None:
    checks = get_gate_checks("demo-tenant", jurisdiction="demo")
    names = " ".join(str(c["name"]) for c in checks)
    for needle in ("KYC/AML", "Responsible gaming", "Audit trail", "RNG"):
        assert needle in names, f"Expected compliance control {needle!r} in gate"


def test_br_jurisdiction_adds_sigap_reporting() -> None:
    br = get_gate_checks("demo-tenant", jurisdiction="br")
    br_blob = " ".join(str(c["name"]) for c in br)
    assert "SIGAP" in br_blob, "br jurisdiction must add SIGAP regulatory reporting"

    # a different jurisdiction must NOT mention SIGAP
    mga = get_gate_checks("demo-tenant", jurisdiction="mga")
    mga_blob = " ".join(str(c["name"]) for c in mga)
    assert "SIGAP" not in mga_blob, "SIGAP must be specific to br jurisdiction"
    assert "MGA" in mga_blob, "mga jurisdiction must add MGA reporting"


def test_gate_checks_disjoint_between_tenants() -> None:
    a = get_gate_checks("tenant-a", jurisdiction="br")
    b = get_gate_checks("tenant-b", jurisdiction="br")
    # namespaced commands must reference the respective tenant, never the other
    a_cmds = " ".join(str(c["command"]) for c in a)
    b_cmds = " ".join(str(c["command"]) for c in b)
    assert "tenant-a" in a_cmds and "tenant-b" not in a_cmds
    assert "tenant-b" in b_cmds and "tenant-a" not in b_cmds


def test_provision_flow_runs_gate_before_completion() -> None:
    steps = get_provision_steps(
        "demo-tenant", domain="acmetocasino.com", jurisdiction="br"
    )
    names = [str(s["name"]) for s in steps]

    gate_idx = next(
        i for i, n in enumerate(names) if "security + compliance gate" in n.lower()
    )
    smoke_idx = next(i for i, n in enumerate(names) if "smoke test" in n.lower())

    # gate must run BEFORE the final smoke test / completion
    assert gate_idx < smoke_idx, "Gate must run before the smoke test"
    # gate must run AFTER deploy + migrations (i.e. not the very first step)
    assert gate_idx > 0, "Gate must run after deploy/migrate steps"

    # the gate step delegates to `caasctl verify`
    gate_cmd = str(steps[gate_idx]["command"])
    assert "verify" in gate_cmd, "Gate step must invoke caasctl verify"
    assert "demo-tenant" in gate_cmd


# ---------------------------------------------------------------------------
# Secrets / HSM provisioning (per-tenant OpenBao AppRole + transit key)
# ---------------------------------------------------------------------------


def test_provision_renders_manifests_and_sets_up_secrets() -> None:
    steps = get_provision_steps("demo-tenant", jurisdiction="br")
    blob = " ".join(str(s["name"]) + " " + str(s["command"]) for s in steps)
    assert "render_manifests.sh" in blob, "provision must render per-tenant manifests"
    assert "setup_tenant_secrets.sh" in blob, "provision must set up per-tenant secrets/HSM"
    assert "manifests/rendered/demo-tenant/" in blob, "provision must apply rendered manifests"


def test_secrets_and_render_steps_are_slug_scoped() -> None:
    a = " ".join(str(s["command"]) for s in get_provision_steps("tenant-a", jurisdiction="br"))
    b = " ".join(str(s["command"]) for s in get_provision_steps("tenant-b", jurisdiction="br"))
    assert "tenant-a" in a and "tenant-b" not in a
    assert "tenant-b" in b and "tenant-a" not in b


def test_gate_includes_hsm_transit_check() -> None:
    checks = get_gate_checks("demo-tenant", jurisdiction="br")
    blob = " ".join(str(c["name"]) + " " + str(c["command"]) for c in checks)
    assert "HSM" in blob, "gate must verify the HSM-backed transit key"
    assert "transit/keys/caas-demo-tenant" in blob, "gate must check the per-tenant transit key"
    hsm = next(c for c in checks if "HSM" in str(c["name"]))
    assert str(hsm["category"]) == "security"
