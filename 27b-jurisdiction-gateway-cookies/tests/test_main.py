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

import json
import os
import sys
import textwrap
from pathlib import Path

# Make the gateway package importable when pytest is run from repo root.
sys.path.insert(0, "/opt/jurisdiction-gateway")

import pytest
import fakeredis
from fastapi import HTTPException
from fastapi.testclient import TestClient


# Public test certificate only (no key); used to build an SSL context, never to connect.
_SELF_SIGNED_PEM = """-----BEGIN CERTIFICATE-----
MIIDETCCAfmgAwIBAgIUPFJnQDMd1aQWYEVHXIKeb/jMuwAwDQYJKoZIhvcNAQEL
BQAwFzEVMBMGA1UEAwwMYmFvLmludGVybmFsMCAXDTI2MTEyMTIyNTUzNloYDzIx
MjYxMDI4MjI1NTM2WjAXMRUwEwYDVQQDDAxiYW8uaW50ZXJuYWwwggEiMA0GCSqG
SIb3DQEBAQUAA4IBDwAwggEKAoIBAQC23fTk5aKIyPPPfzFqYvBBOXS55XihChgn
pXD+GO4akbTInsV4Wi56eJBx8i3tRxmtILxCKe9ZV7ECKypN8dDJr45L4CNH5zRA
ze67lBY3KfJSUd+8aznzlS706qtoysCGIm5hea2DbsUFVdyFnxikk98zCWOKHnXu
tOqZXvRBPAbz7W+g0irHZd4WAk5HeFREj8xITkDrcZjb5DlEehptvn7wb8n4Xfn3
7CgralMWTVDabOH1XJgFE7hcJm1E3sX6nbzxM8Iq36XVrocCImQZabG1sbgzCb4c
vTssNwgimY6Z1jeFoBS8JUI/PLkTx9c/bvTYRXc4YAIoF8fT3qd7AgMBAAGjUzBR
MB0GA1UdDgQWBBQrRtrgwUoQFekGQMnGvEa24d9iDTAfBgNVHSMEGDAWgBQrRtrg
wUoQFekGQMnGvEa24d9iDTAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUA
A4IBAQAMkVAaIpXvSqrOXIsQDLA5/G229xASeIWO0KJzqWSYtPQ5drY9ZnGYegro
tGWDhd8yG+LS7F5zJOidq8zXJ6W6gIjQksj0Fv2QG7vS/K5KuBVPTwTek0TsBjyf
+IcU1+6dPTyvASm35koCyEf8xVjUZQjMl9Ys73mWkbaQeyBETPO+Q/vHNfMNjN4o
vzffLT5woSE9tQGEX5IC+8XHig9wwEARkPygnsAPG4FReHGYZ0pA6ObxVSMx0iLN
RWvGzP0RBqk1f0CQdrYQlr+dp0oOtwUuCP3lvo2gztVez+4Ze0160kseU4EHyfoy
RhqhBQs2lEaALyACliyv4ZA3Zz6j
-----END CERTIFICATE-----
"""


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
    if log.exists():
        lines = log.read_text().strip().splitlines()
    if not lines and m.DECISIONS_LOG.exists():
        lines = m.DECISIONS_LOG.read_text().strip().splitlines()
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


def test_http_reload_no_admin_token_denied(fresh_env, monkeypatch):
    """No admin token configured: reload fails closed, for header and query alike."""
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "")
    client = TestClient(m.app)
    assert client.post("/v1/reload").status_code == 403
    assert client.post("/v1/reload", headers={"Authorization": "Bearer "}).status_code == 403
    assert client.post("/v1/reload?token=").status_code == 403


def test_http_reload_with_admin_token_rejected(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    client = TestClient(m.app)
    assert client.post("/v1/reload", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.post("/v1/reload").status_code == 401


def test_http_reload_token_in_query_string_ignored(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    client = TestClient(m.app)
    r = client.post("/v1/reload?token=secret")
    assert r.status_code == 401


def test_http_reload_with_bearer_ok(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    client = TestClient(m.app)
    r = client.post("/v1/reload", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["count"] == 6


def test_reload_uses_constant_time_compare(fresh_env):
    import inspect
    assert "compare_digest" in inspect.getsource(fresh_env._require_bearer)


# ---- reload validates first and keeps the old rules on failure ----

def _bad_reload(m, monkeypatch, content):
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    m.RULES_PATH.write_text(content)
    client = TestClient(m.app)
    before_sha = m.redis_client().get("jgw:meta:sha256")
    r = client.post("/v1/reload", headers={"Authorization": "Bearer secret"})
    return r, before_sha


@pytest.mark.parametrize("content", [
    "rules: [unterminated",                        # broken YAML
    "rules:\n  - from_jurisdiction: EU-DE\n",     # rule missing required fields
    "rules: []\n",                                 # empty rule set
    "- just\n- a list\n",                         # wrong shape
])
def test_reload_malformed_keeps_old_rules(fresh_env, monkeypatch, content):
    m = fresh_env
    r, before_sha = _bad_reload(m, monkeypatch, content)
    assert r.status_code == 422
    rd = m.redis_client()
    assert int(rd.get("jgw:meta:count")) == 6
    assert rd.get("jgw:meta:sha256") == before_sha
    resp = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII"))
    assert resp.allowed is True


def test_reload_missing_file_keeps_old_rules(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    m.RULES_PATH.unlink()
    client = TestClient(m.app)
    r = client.post("/v1/reload", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 422
    assert int(m.redis_client().get("jgw:meta:count")) == 6


def test_reload_swaps_rules(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    m.RULES_PATH.write_text("rules:\n  - from_jurisdiction: AA\n    to_destination: BB\n"
                            "    data_class: X\n    mechanism: adequacy\n")
    client = TestClient(m.app)
    r = client.post("/v1/reload", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.json()["count"] == 1
    old = m.evaluate(m.EvalRequest(
        from_jurisdiction="EU-DE", to_destination="Cloudflare-US", data_class="PII"))
    assert old.mechanism == "no-rule"


def test_reload_clears_session_cache(fresh_env, monkeypatch):
    m = fresh_env
    req = m.EvalRequest(from_jurisdiction="EU-DE", to_destination="Cloudflare-US",
                        data_class="PII", session_id="sess-reload")
    first = m.evaluate(req)
    assert first.allowed is True
    # Operator revokes the transfer and reloads.
    monkeypatch.setattr(m, "ADMIN_TOKEN", "secret")
    m.RULES_PATH.write_text("rules:\n  - from_jurisdiction: EU-DE\n"
                            "    to_destination: Cloudflare-US\n    data_class: PII\n"
                            "    mechanism: forbidden\n    allowed: false\n")
    client = TestClient(m.app)
    assert client.post("/v1/reload", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert list(m.redis_client().scan_iter("jgw:sess:*")) == []
    second = m.evaluate(req)
    assert second.allowed is False
    assert second.cached is False


def test_failed_reload_keeps_session_cache(fresh_env, monkeypatch):
    m = fresh_env
    req = m.EvalRequest(from_jurisdiction="EU-DE", to_destination="Cloudflare-US",
                        data_class="PII", session_id="sess-keep")
    m.evaluate(req)
    r, _ = _bad_reload(m, monkeypatch, "rules: [unterminated")
    assert r.status_code == 422
    assert m.evaluate(req).cached is True


# ---- audit write failure refuses the decision ----

def test_audit_write_failure_refuses_decision(fresh_env, monkeypatch):
    m = fresh_env
    bad = m.DECISIONS_LOG.parent / "no-such-dir" / "decisions.jsonl"
    monkeypatch.setattr(m, "DECISIONS_LOG", bad)
    with pytest.raises(HTTPException) as exc:
        m.evaluate(m.EvalRequest(from_jurisdiction="EU-DE", to_destination="Cloudflare-US",
                                 data_class="PII", session_id="sess-audit"))
    assert exc.value.status_code == 503
    # Not cached either: a later call must not replay a decision that was never recorded.
    assert list(m.redis_client().scan_iter("jgw:sess:*")) == []


def test_http_evaluate_503_when_audit_unavailable(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "DECISIONS_LOG", m.DECISIONS_LOG.parent / "nope" / "d.jsonl")
    client = TestClient(m.app)
    r = client.post("/v1/evaluate", json={
        "from_jurisdiction": "EU-DE", "to_destination": "Cloudflare-US", "data_class": "PII"})
    assert r.status_code == 503
    assert "allowed" not in r.json()


# ---- crypto routes: caller auth, no echo ----

@pytest.fixture
def crypto_env(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "CRYPTO_ENCRYPT_TOKEN", "enc-secret")
    monkeypatch.setattr(m, "CRYPTO_DECRYPT_TOKEN", "dec-secret")
    calls = []

    def fake_bao(path, body):
        calls.append(path)
        if path.startswith("transit/encrypt"):
            return {"data": {"ciphertext": "vault:v1:abc"}}
        return {"data": {"plaintext": "aGVsbG8="}}  # "hello"

    monkeypatch.setattr(m, "_bao_call", fake_bao)
    return m, TestClient(m.app), calls


def test_crypto_denied_when_tokens_unset(fresh_env):
    m = fresh_env
    assert m.CRYPTO_ENCRYPT_TOKEN == "" and m.CRYPTO_DECRYPT_TOKEN == ""
    client = TestClient(m.app)
    h = {"Authorization": "Bearer anything"}
    assert client.post("/v1/crypto/encrypt", json={"plaintext": "x"}, headers=h).status_code == 403
    assert client.post("/v1/crypto/decrypt", json={"ciphertext": "vault:v1:x"},
                       headers=h).status_code == 403
    # An empty bearer must not match an empty secret.
    assert client.post("/v1/crypto/decrypt", json={"ciphertext": "vault:v1:x"},
                       headers={"Authorization": "Bearer "}).status_code == 403


def test_crypto_requires_token(crypto_env):
    _, client, calls = crypto_env
    assert client.post("/v1/crypto/encrypt", json={"plaintext": "x"}).status_code == 401
    assert client.post("/v1/crypto/decrypt", json={"ciphertext": "vault:v1:x"}).status_code == 401
    assert calls == []


def test_crypto_wrong_and_swapped_tokens_rejected(crypto_env):
    _, client, calls = crypto_env
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/v1/crypto/encrypt", json={"plaintext": "x"}, headers=wrong).status_code == 403
    # The encrypt secret does not open decrypt, and the other way round.
    enc = {"Authorization": "Bearer enc-secret"}
    dec = {"Authorization": "Bearer dec-secret"}
    assert client.post("/v1/crypto/decrypt", json={"ciphertext": "vault:v1:x"},
                       headers=enc).status_code == 403
    assert client.post("/v1/crypto/encrypt", json={"plaintext": "x"}, headers=dec).status_code == 403
    assert calls == []


def test_crypto_happy_path(crypto_env):
    _, client, _ = crypto_env
    r = client.post("/v1/crypto/encrypt", json={"plaintext": "hello"},
                    headers={"Authorization": "Bearer enc-secret"})
    assert r.status_code == 200 and r.json()["ciphertext"] == "vault:v1:abc"
    r = client.post("/v1/crypto/decrypt", json={"ciphertext": "vault:v1:abc"},
                    headers={"Authorization": "Bearer dec-secret"})
    assert r.status_code == 200 and r.json()["plaintext"] == "hello"


@pytest.mark.parametrize("body", [{"ciphertext": "not-a-ciphertext"}, {"ciphertext": ""}, {}])
def test_decrypt_never_echoes_unknown_input(crypto_env, body):
    _, client, calls = crypto_env
    r = client.post("/v1/crypto/decrypt", json=body, headers={"Authorization": "Bearer dec-secret"})
    assert r.status_code == 422
    assert "not-a-ciphertext" not in r.text
    assert "plaintext" not in r.json()
    assert calls == []


# ---- TLS to OpenBao is verified ----

def test_bao_context_verifies_certificate_and_hostname(fresh_env, monkeypatch, tmp_path):
    import ssl
    m = fresh_env
    ca = tmp_path / "ca-test.pem"
    ca.write_text(_SELF_SIGNED_PEM)
    monkeypatch.setattr(m, "_BAO_CACERT", str(ca))
    monkeypatch.setattr(m, "_BAO_ADDR", "https://bao.internal:8200")
    ctx = m._bao_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_bao_context_refused_without_cacert(fresh_env, monkeypatch):
    m = fresh_env
    monkeypatch.setattr(m, "_BAO_CACERT", "")
    with pytest.raises(HTTPException) as exc:
        m._bao_ssl_context()
    assert exc.value.status_code == 503


def test_bao_context_refused_for_plain_http(fresh_env, monkeypatch, tmp_path):
    m = fresh_env
    monkeypatch.setattr(m, "_BAO_CACERT", str(tmp_path / "ca.pem"))
    monkeypatch.setattr(m, "_BAO_ADDR", "http://bao.internal:8200")
    with pytest.raises(HTTPException) as exc:
        m._bao_ssl_context()
    assert exc.value.status_code == 503


def test_bao_context_refused_for_unreadable_cacert(fresh_env, monkeypatch, tmp_path):
    m = fresh_env
    monkeypatch.setattr(m, "_BAO_CACERT", str(tmp_path / "missing.pem"))
    with pytest.raises(HTTPException) as exc:
        m._bao_ssl_context()
    assert exc.value.status_code == 503


def test_source_has_no_unverified_tls():
    src = (Path(__file__).parent.parent / "app" / "main.py").read_text()
    assert "_create_unverified_context" not in src
    assert "CERT_NONE" not in src
    assert "check_hostname = False" not in src
