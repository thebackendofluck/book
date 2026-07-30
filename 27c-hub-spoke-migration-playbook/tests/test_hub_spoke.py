# Companion code for "The Backend of Luck" - Chapter 27c, Migrating a Single-Jurisdiction Casino Platform to Hub & Spo.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""End-to-end validation of the hub-and-spoke reference.

Assumes three local port-forwards are already running on the ops-host host:
  - localhost:18000 -> global-id.hub.svc.cluster.local:8000
  - localhost:18001 -> mailer.hub.svc.cluster.local:8000
  - localhost:18002 -> wallet-br.spoke-br.svc.cluster.local:8000

The fifth scenario (NetworkPolicy proof) shells out to `kubectl` directly
since it exercises cluster networking, not an HTTP endpoint.
"""
from __future__ import annotations

import os
import random
import subprocess
import time
import uuid

import httpx
import pytest

GLOBAL_ID = os.environ.get("GLOBAL_ID_URL", "http://127.0.0.1:18000")
MAILER = os.environ.get("MAILER_URL", "http://127.0.0.1:18001")
WALLET_BR = os.environ.get("WALLET_BR_URL", "http://127.0.0.1:18002")
KUBECTL = os.environ.get(
    "KUBECTL", "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl"
)


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    with httpx.Client(timeout=15.0) as c:
        yield c


@pytest.fixture(scope="session")
def registered_player(http: httpx.Client) -> dict:
    """Create a fresh spoke player end-to-end; returns {local, global}."""
    local = str(uuid.uuid4())
    r = http.post(f"{WALLET_BR}/v1/players", json={"local_player_id": local})
    assert r.status_code == 200, r.text
    return {"local": local, "global": r.json()["global_id"]}


def test_1_register_player_gets_global_id(http: httpx.Client) -> None:
    local = str(uuid.uuid4())
    r = http.post(f"{WALLET_BR}/v1/players", json={"local_player_id": local})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["local_player_id"] == local
    # Valid UUID confirms the hub minted one.
    uuid.UUID(body["global_id"])

    # Hub can be queried directly and agrees.
    r2 = http.get(
        f"{GLOBAL_ID}/v1/players/by-local",
        params={"jurisdiction": "BR", "local_player_id": local},
    )
    assert r2.status_code == 200
    assert r2.json()["global_id"] == body["global_id"]


def test_2_global_exclusion_propagates_to_spoke(
    http: httpx.Client, registered_player: dict
) -> None:
    gid = registered_player["global"]
    local = registered_player["local"]
    r = http.post(
        f"{GLOBAL_ID}/v1/players/{gid}/exclude",
        json={"reason": "pytest-global-exclusion", "scope": "global"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "globally_excluded"

    # Spoke subscriber should flip status within 5s.
    deadline = time.time() + 5.0
    status = None
    while time.time() < deadline:
        bal = http.get(f"{WALLET_BR}/v1/wallet/balance", params={"player_id": local})
        assert bal.status_code == 200
        status = bal.json()["status"]
        if status == "globally_excluded":
            break
        time.sleep(0.25)
    assert status == "globally_excluded", f"spoke did not sync within 5s (last={status!r})"


def test_3_deposit_rejected_for_excluded_player(
    http: httpx.Client, registered_player: dict
) -> None:
    # Player from fixture was excluded in test_2.
    r = http.post(
        f"{WALLET_BR}/v1/wallet/deposit",
        json={"player_id": registered_player["local"], "amount_cents": 1000},
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "player_excluded"
    assert detail["status"] == "globally_excluded"


def test_4_mailer_opt_in_gating(http: httpx.Client) -> None:
    # Fresh player just for this scenario.
    local = str(uuid.uuid4())
    rp = http.post(f"{WALLET_BR}/v1/players", json={"local_player_id": local})
    assert rp.status_code == 200
    gid = rp.json()["global_id"]

    # Explicitly deny newsletter opt-in for BR.
    r = http.post(
        f"{MAILER}/v1/opt-in",
        json={
            "global_id": gid,
            "jurisdiction": "BR",
            "category": "newsletter",
            "granted": False,
        },
    )
    assert r.status_code == 200

    # Newsletter send should be suppressed.
    r = http.post(
        f"{MAILER}/v1/send",
        json={
            "to_player_global_id": gid,
            "template": "newsletter",
            "jurisdiction": "BR",
            "data": {"name": "Ana"},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "sent": False,
        "suppressed": True,
        "reason": "opt_in_missing_or_denied",
    }

    # Transactional dsr_ack bypasses opt-in and sends.
    r = http.post(
        f"{MAILER}/v1/send",
        json={
            "to_player_global_id": gid,
            "template": "dsr_ack",
            "jurisdiction": "BR",
            "data": {"name": "Ana", "ref": "DSR-1", "date": "2026-04-13"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True
    assert body["suppressed"] is False
    assert "DSR-1" in body["rendered_body"]


def _tcp_probe_from_wallet_br(host: str, port: int) -> int:
    """Run a short TCP connect from the wallet-br pod, returning exit code.

    Exec'ing into a long-lived pod (rather than kubectl run --rm) dodges a
    kube-router quirk where per-pod firewall chains are not yet installed
    for ephemeral short-lived pods, which makes NP enforcement racy for them.
    """
    py = (
        "import socket,sys;"
        f"s=socket.socket();s.settimeout(4);"
        f"sys.exit(0 if s.connect_ex(({host!r},{port}))==0 else 1)"
    )
    cmd = (
        f"{KUBECTL} -n spoke-br exec deploy/wallet-br -c wallet-br -- "
        f"python3 -c \"{py}\""
    )
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=30
    )
    return result.returncode


def test_5_networkpolicy_blocks_spoke_to_hub_db() -> None:
    """Spoke pods can reach hub-redis and global-id, but NOT hub-postgres."""
    # Sanity: allowlisted destinations remain reachable.
    assert _tcp_probe_from_wallet_br("global-id.hub.svc.cluster.local", 8000) == 0, (
        "allowlisted hub service global-id:8000 should be reachable"
    )
    assert _tcp_probe_from_wallet_br("hub-redis.hub.svc.cluster.local", 6379) == 0, (
        "allowlisted hub service hub-redis:6379 should be reachable"
    )
    # Blocked: hub-postgres is NOT in the allowlist.
    rc = _tcp_probe_from_wallet_br("hub-postgres.hub.svc.cluster.local", 5432)
    assert rc != 0, "spoke reached hub-postgres (NetworkPolicy not enforcing)"
