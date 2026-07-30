# Companion code for "The Backend of Luck" - Chapter 27b, The Jurisdiction Transfer Gateway and Cookie Consent.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""pytest suite for jurisdiction-gateway.

Uses fakeredis so tests don't require a real Redis. Each test gets a fresh
Redis + fresh rules file + fresh SQLite audit DB, so they're deterministic
and can run in any order.
"""

from __future__ import annotations

import sys, os, tempfile, textwrap, json, importlib
from pathlib import Path

# Make the gateway package importable when pytest is run from repo root.
sys.path.insert(0, "/opt/jurisdiction-gateway")

import pytest
import fakeredis
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_env(tmp_path, monkeypatch):
    """Fresh rules.yaml + fresh SQLite per test. Reload the app module."""
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(textwrap.dedent("""\
        rules:
          - from_jurisdiction: EU-DE
            to_destination: Cloudflare-US
            data_class: PII
            mechanism: DPF
            tia_required: true
            review_by: "2026-07-10T00:00:00Z"
            citation: "EU-US DPF 2023/1795"
          - from_jurisdiction: EU-FR
            to_destination: Meta-US
            data_class: marketing
            mechanism: forbidden
            allowed: false
            citation: "CNIL 2022-010"
          - from_jurisdiction: UK
            to_destination: BR
            data_class: KYC
            mechanism: SCCs-2021-914-M1
            tia_required: true
            citation: "UK IDTA 2022"
          - from_jurisdiction: EU-IE
            to_destination: EU-DE
            data_class: PII
            mechanism: intra-EEA
            citation: "Chapter V not triggered"
          - from_jurisdiction: EU-IE
            to_destination: EXPIRED-DEST
            data_class: PII
            mechanism: adequacy
            expires_at: "2020-01-01T00:00:00Z"
            citation: "hypothetical old adequacy"
          - from_jurisdiction: EU-IE
            to_destination: SOON-EXPIRE
            data_class: PII
            mechanism: adequacy
            review_by: "2026-05-01T00:00:00Z"
            citation: "hypothetical near-expiry"
        """))
    monkeypatch.setenv("JGW_RULES", str(rules_path))
    monkeypatch.setenv("JGW_SQLITE", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JGW_LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setenv("JGW_SESSION_TTL", "1800")

    # Reload module so module-level Path defaults pick up env vars.
    if "app.main" in sys.modules:
        del sys.modules["app.main"]
    if "app" in sys.modules:
        del sys.modules["app"]
    import app.main as m

    fake = fakeredis.FakeRedis(decode_responses=True)
    m.set_redis(fake)
    m.init_db()
    m.load_rules()
    return m


def test_rules_loaded(fresh_env):
    m = fresh_env
    r = m.redis_client()
    assert int(r.get("jgw:meta:count")) == 6
    assert len(r.get("jgw:meta:sha256")) == 64


def test_eval_dpf_allowed_with_tia(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII"))
    assert resp.allowed is True
    assert resp.mechanism == "DPF"
    assert resp.tia_required is True
    assert "Schrems II" in resp.reasoning


def test_eval_forbidden(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-FR", to_destination="Meta-US", data_class="marketing"))
    assert resp.allowed is False
    assert resp.mechanism == "forbidden"


def test_eval_scc(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="UK", to_destination="BR", data_class="KYC"))
    assert resp.allowed is True
    assert resp.mechanism.startswith("SCCs")
    assert resp.tia_required is True


def test_eval_intra_eea(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-IE", to_destination="EU-DE", data_class="PII"))
    assert resp.allowed is True
    assert resp.mechanism == "intra-EEA"


def test_eval_fail_safe_deny_unknown(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-IT", to_destination="RU-Yandex", data_class="PII"))
    assert resp.allowed is False
    assert resp.mechanism == "no-rule"
    assert "Fail-safe" in resp.reasoning


def test_eval_expired_rule_denies(fresh_env):
    m = fresh_env
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-IE", to_destination="EXPIRED-DEST", data_class="PII"))
    assert resp.allowed is False
    assert resp.mechanism == "expired"


def test_session_cache(fresh_env):
    m = fresh_env
    r1 = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII",
        session_id="sess-abc"))
    r2 = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII",
        session_id="sess-abc"))
    assert r1.decision_id == r2.decision_id
    assert r1.cached is False
    assert r2.cached is True


def test_session_cache_isolates_sessions(fresh_env):
    m = fresh_env
    r1 = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII",
        session_id="sess-alice"))
    r2 = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII",
        session_id="sess-bob"))
    assert r1.decision_id != r2.decision_id


def test_audit_log_appends(fresh_env, tmp_path):
    m = fresh_env
    m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII"))
    log = Path(os.environ["JGW_LOG_DIR"]) / "decisions.jsonl"
    # DECISIONS_LOG module-level captured LOG_DIR at import time; check both locations.
    lines = []
    if log.exists(): lines = log.read_text().strip().splitlines()
    if not lines and m.DECISIONS_LOG.exists(): lines = m.DECISIONS_LOG.read_text().strip().splitlines()
    assert len(lines) >= 1
    obj = json.loads(lines[-1])
    assert obj["from"] == "EU-DE" and obj["to"] == "Cloudflare-US"
    assert obj["allowed"] is True
    assert "decision_id" in obj


def test_audit_sqlite_insert(fresh_env):
    m = fresh_env
    m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII",
        player_hash="sha256:deadbeef"))
    with m.db_cursor() as cur:
        cur.execute("SELECT count(*) FROM jgw_decisions")
        assert cur.fetchone()[0] >= 1
        cur.execute("SELECT player_hash FROM jgw_decisions WHERE player_hash IS NOT NULL")
        assert cur.fetchone()[0] == "sha256:deadbeef"


def test_rules_expiring_surfaces_near_expiry(fresh_env):
    m = fresh_env
    # within_days=90 should catch SOON-EXPIRE (2026-05-01) but not 2028-06-27.
    client = TestClient(m.app)
    r = client.get("/v1/rules/expiring?within_days=90")
    assert r.status_code == 200
    bodies = {rule["to_destination"] for rule in r.json()["rules"]}
    assert "SOON-EXPIRE" in bodies


def test_http_evaluate_endpoint(fresh_env):
    m = fresh_env
    client = TestClient(m.app)
    r = client.post("/v1/evaluate", json={
        "from_jurisdiction": "EU-DE", "to_destination": "Cloudflare-US",
        "data_class": "PII", "session_id": "http-s1"
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["mechanism"] == "DPF"


def test_http_readyz_200(fresh_env):
    m = fresh_env
    client = TestClient(m.app)
    r = client.get("/v1/readyz")
    assert r.status_code == 200
    assert r.json()["rules_count"] == 6


def test_http_reload_no_admin_token_allowed(fresh_env, tmp_path, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "")  # explicit: no token required
    client = TestClient(m.app)
    r = client.post("/v1/reload")
    assert r.status_code == 200
    assert r.json()["count"] == 6


def test_http_reload_with_admin_token_rejected(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    client = TestClient(m.app)
    r = client.post("/v1/reload?token=wrong")
    assert r.status_code == 403
