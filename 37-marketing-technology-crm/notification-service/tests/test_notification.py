# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Tests for the Notification Service.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# notification-service ships its own `models.py`/`main.py` which
# collide with other chapters' same-named modules during a full-repo
# pytest run. Pre-install the local copies via importlib so we don't
# depend on sys.modules ordering.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str) -> None:
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


_load_local_module("models", "models.py")
_load_local_module("dispatcher", "dispatcher.py")
_load_local_module("main", "main.py")


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Module-scoped re-pin so sibling chapters can't poison
    sys.modules while our test bodies are running."""
    _load_local_module("models", "models.py")
    _load_local_module("dispatcher", "dispatcher.py")
    _load_local_module("main", "main.py")
    yield


from main import app, _templates, _history
from models import NotificationType, NotificationStatus, Template


@pytest.fixture(autouse=True)
def clear_stores():
    """Reset in-memory stores before each test."""
    _templates.clear()
    _history.clear()
    yield
    _templates.clear()
    _history.clear()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Template management tests
# ---------------------------------------------------------------------------

def test_create_template(client):
    tmpl = {
        "name": "welcome_email",
        "channel": "EMAIL",
        "subject": "Welcome ${player_name}!",
        "body": "Hi ${player_name}, your account is ready.",
        "active": True,
    }
    resp = client.post("/templates", json=tmpl)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "welcome_email"
    assert "template_id" in data


def test_list_templates_empty(client):
    resp = client.get("/templates")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_templates_returns_active(client):
    for name, active in [("t1", True), ("t2", False)]:
        client.post("/templates", json={
            "name": name, "channel": "SMS", "body": "hello", "active": active
        })
    resp = client.get("/templates?active_only=true")
    names = [t["name"] for t in resp.json()]
    assert "t1" in names
    assert "t2" not in names


def test_list_templates_all_when_not_active_only(client):
    for name, active in [("t1", True), ("t2", False)]:
        client.post("/templates", json={
            "name": name, "channel": "SMS", "body": "hello", "active": active
        })
    resp = client.get("/templates?active_only=false")
    assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# Notification dispatch tests
# ---------------------------------------------------------------------------

def test_send_notification_with_template(client):
    client.post("/templates", json={
        "name": "deposit_confirm", "channel": "EMAIL",
        "subject": "Deposit of ${amount} received",
        "body": "Dear ${player_name}, we received ${amount}.",
        "active": True,
    })
    resp = client.post("/notify", json={
        "player_id": "player_001",
        "channel": "EMAIL",
        "template_name": "deposit_confirm",
        "variables": {"player_name": "Alice", "amount": "€100"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == NotificationStatus.SENT
    assert "Alice" in data["rendered_body"]
    assert "€100" in data["rendered_body"]


def test_send_notification_raw_body(client):
    resp = client.post("/notify", json={
        "player_id": "player_002",
        "channel": "SMS",
        "body": "Your OTP is ${code}",
        "variables": {"code": "123456"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == NotificationStatus.SENT
    assert "123456" in data["rendered_body"]


def test_send_notification_missing_template_raises_404(client):
    resp = client.post("/notify", json={
        "player_id": "player_003",
        "channel": "EMAIL",
        "template_name": "nonexistent_template",
    })
    assert resp.status_code == 404


def test_send_notification_no_body_no_template_raises_422(client):
    resp = client.post("/notify", json={
        "player_id": "player_004",
        "channel": "PUSH",
    })
    assert resp.status_code == 422


def test_send_push_notification(client):
    resp = client.post("/notify", json={
        "player_id": "player_005",
        "channel": "PUSH",
        "body": "You have a new bonus!",
    })
    assert resp.status_code == 201
    assert resp.json()["channel"] == "PUSH"


def test_send_in_app_notification(client):
    resp = client.post("/notify", json={
        "player_id": "player_006",
        "channel": "IN_APP",
        "body": "Level up achieved!",
    })
    assert resp.status_code == 201
    assert resp.json()["channel"] == "IN_APP"


# ---------------------------------------------------------------------------
# History tests
# ---------------------------------------------------------------------------

def test_history_empty_for_unknown_player(client):
    resp = client.get("/history/unknown_player")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_returns_player_notifications(client):
    for i in range(3):
        client.post("/notify", json={
            "player_id": "player_007",
            "channel": "SMS",
            "body": f"Message {i}",
        })
    # Add a notification for a different player
    client.post("/notify", json={
        "player_id": "other_player",
        "channel": "SMS",
        "body": "Not for player_007",
    })
    resp = client.get("/history/player_007")
    data = resp.json()
    assert len(data) == 3
    assert all(n["player_id"] == "player_007" for n in data)


def test_history_limit_parameter(client):
    for i in range(10):
        client.post("/notify", json={
            "player_id": "player_008",
            "channel": "IN_APP",
            "body": f"Notification {i}",
        })
    resp = client.get("/history/player_008?limit=5")
    assert len(resp.json()) == 5


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "templates_count" in data
    assert "history_count" in data
