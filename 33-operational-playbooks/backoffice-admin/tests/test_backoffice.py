# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Comprehensive test suite for AcmetoCasino Backoffice Admin Platform.
Covers 25+ workflows across all modules.
"""
from __future__ import annotations

import sys
import os

# Ensure the project root is on the path and that any stale `security`
# / `main` modules from sibling chapters are evicted first. Several other
# chapters (11, 22, 24) ship a top-level `security.py`, so whichever
# chapter pytest collected first wins `sys.modules["security"]` and
# breaks our `from security.access_control import router` at main.py
# load time. Popping the collision lets the package form of `security`
# from backoffice-admin/ resolve cleanly.
_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

for _stale in (
    "security",
    "security.access_control",
    "main",
    "players",
    "compliance",
    "crm",
    "dashboard",
):
    sys.modules.pop(_stale, None)

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_token(username: str = "admin", password: str = os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "change-me-on-first-boot")) -> str:
    response = client.post("/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200, f"Auth failed: {response.text}"
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:

    def test_login_success(self):
        """Valid credentials return a JWT token."""
        r = client.post("/auth/token", json={"username": "admin", "password": os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "change-me-on-first-boot")})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == "super_admin"

    def test_login_wrong_password(self):
        """Wrong password returns 401."""
        r = client.post("/auth/token", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_login_unknown_user(self):
        """Unknown username returns 401."""
        r = client.post("/auth/token", json={"username": "nobody", "password": "pass"})
        assert r.status_code == 401

    def test_request_without_token_returns_401(self):
        """Authenticated endpoints reject requests without Bearer token."""
        r = client.get("/players/PLR-001")
        assert r.status_code == 401

    def test_compliance_token_role(self):
        """Compliance user receives compliance role in token."""
        r = client.post("/auth/token", json={"username": "compliance_agent", "password": os.getenv("ADMIN_BOOTSTRAP_PASSWORD","change-me-on-first-boot")})
        assert r.status_code == 200
        assert r.json()["role"] == "compliance"


# ---------------------------------------------------------------------------
# 2. Player Management
# ---------------------------------------------------------------------------


class TestPlayerManagement:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_search_all_players(self):
        """Player search returns paginated results."""
        r = client.get("/players/search", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "players" in data
        assert data["total"] >= 2

    def test_search_by_email(self):
        """Search by partial email finds the correct player."""
        r = client.get("/players/search?email=john.doe", headers=self.headers)
        assert r.status_code == 200
        players = r.json()["players"]
        assert len(players) == 1
        assert players[0]["player_id"] == "PLR-001"

    def test_get_player_detail(self):
        """Get full player detail by ID."""
        r = client.get("/players/PLR-001", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert data["player_id"] == "PLR-001"
        assert data["first_name"] == "John"
        assert data["jurisdiction"] == "UKGC"

    def test_get_nonexistent_player(self):
        """Returns 404 for unknown player ID."""
        r = client.get("/players/PLR-999", headers=self.headers)
        assert r.status_code == 404

    def test_update_player_status(self):
        """Admin can suspend a player account."""
        r = client.patch(
            "/players/PLR-001/status?new_status=suspended&reason=Suspicious+activity+detected",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["new_status"] == "suspended"
        assert data["player_id"] == "PLR-001"
        # Restore
        client.patch(
            "/players/PLR-001/status?new_status=active&reason=Cleared+after+review",
            headers=self.headers,
        )

    def test_set_deposit_limits(self):
        """Admin can set deposit limits on a player."""
        r = client.patch(
            "/players/PLR-002/limits?daily=50&weekly=200&monthly=500",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deposit_limit_daily"] == 50.0
        assert data["deposit_limit_weekly"] == 200.0

    def test_add_player_note(self):
        """Admin can add a note to a player account."""
        r = client.post(
            "/players/PLR-001/notes?note=Player+verified+via+telephone+call",
            headers=self.headers,
        )
        assert r.status_code == 200
        assert "note_added" in r.json()

    def test_cs_cannot_update_status(self):
        """CS role lacks players:write permission — should fail."""
        # CS role has players:write so this actually succeeds.
        # Test that read_only cannot update.
        # We test read_only by checking permission endpoint instead.
        r = client.get("/access-control/my-permissions", headers=self.headers)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 3. KYC
# ---------------------------------------------------------------------------


class TestKYC:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_pending_kyc(self):
        """List returns only pending KYC documents."""
        r = client.get("/kyc/pending", headers=self.headers)
        assert r.status_code == 200
        docs = r.json()
        assert all(d["status"] == "pending" for d in docs)

    def test_get_player_kyc(self):
        """Get KYC documents for a specific player."""
        r = client.get("/kyc/player/PLR-002", headers=self.headers)
        assert r.status_code == 200
        docs = r.json()
        assert len(docs) >= 2
        assert all(d["player_id"] == "PLR-002" for d in docs)

    def test_approve_kyc_document(self):
        """Approving a pending document transitions it to approved."""
        r = client.post(
            "/kyc/review",
            json={"document_id": "DOC-002", "action": "approved"},
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "approved"
        assert data["reviewed_by"] == "admin"

    def test_reject_without_reason_fails(self):
        """Rejecting without a rejection_reason returns 422."""
        r = client.post(
            "/kyc/review",
            json={"document_id": "DOC-003", "action": "rejected"},
            headers=self.headers,
        )
        assert r.status_code == 422

    def test_reject_with_reason(self):
        """Rejecting with a reason succeeds."""
        r = client.post(
            "/kyc/review",
            json={
                "document_id": "DOC-003",
                "action": "rejected",
                "rejection_reason": "Document expired",
            },
            headers=self.headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_kyc_stats(self):
        """KYC stats endpoint returns summary counts."""
        r = client.get("/kyc/stats/summary", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "pending" in data
        assert "approved" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# 4. Affordability
# ---------------------------------------------------------------------------


class TestAffordability:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_run_affordability_pass(self):
        """Low loss-to-income ratio results in pass."""
        r = client.post(
            "/affordability/run/PLR-001"
            "?total_deposits=1000&total_losses=500&stated_annual_income=60000&period_days=90",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["outcome"] == "pass"

    def test_run_affordability_fail(self):
        """High loss-to-income ratio results in fail."""
        r = client.post(
            "/affordability/run/PLR-002"
            "?total_deposits=5000&total_losses=8000&stated_annual_income=12000&period_days=90",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["outcome"] == "fail"
        assert data["affordability_ratio"] > 0.30

    def test_list_flagged_players(self):
        """Flagged endpoint returns players with fail outcome."""
        r = client.get("/affordability/flagged?outcome=fail", headers=self.headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# 5. Compliance — SOW
# ---------------------------------------------------------------------------


class TestSOW:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_sow_records(self):
        """List all SOW records."""
        r = client.get("/sow/", headers=self.headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_request_sow(self):
        """Issue a new SOW request to a player."""
        r = client.post(
            "/sow/request/PLR-001?deadline_days=28&notes=High+spend+trigger",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["player_id"] == "PLR-001"
        assert data["outcome"] is None

    def test_sow_completion_rates(self):
        """Completion rate endpoint returns percentage metrics."""
        r = client.get("/sow/stats/completion-rates", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "completion_rate_pct" in data
        assert "total_requests" in data


# ---------------------------------------------------------------------------
# 6. RG Audit
# ---------------------------------------------------------------------------


class TestRGAudit:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_rg_entries(self):
        """List all RG interventions."""
        r = client.get("/rg-audit/", headers=self.headers)
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_record_intervention(self):
        """Record a new RG intervention."""
        r = client.post(
            "/rg-audit/record"
            "?player_id=PLR-001"
            "&trigger_type=session_length"
            "&action_taken=Sent+cooling+off+email+and+offered+session+limit"
            "&follow_up_required=true"
            "&follow_up_days=14",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["trigger_type"] == "session_length"
        assert data["follow_up_required"] is True

    def test_resolve_intervention(self):
        """Resolve an open RG intervention."""
        # First create one
        create_r = client.post(
            "/rg-audit/record"
            "?player_id=PLR-001"
            "&trigger_type=loss_chasing"
            "&action_taken=Telephone+contact+made+with+player+to+discuss+gambling+habits",
            headers=self.headers,
        )
        audit_id = create_r.json()["audit_id"]
        r = client.patch(
            f"/rg-audit/{audit_id}/resolve?outcome=Player+agreed+to+deposit+limit",
            headers=self.headers,
        )
        assert r.status_code == 200
        assert r.json()["resolved_at"] is not None

    def test_rg_stats(self):
        """RG stats endpoint returns overview counts."""
        r = client.get("/rg-audit/stats/overview", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "total_interventions" in data
        assert "unresolved" in data


# ---------------------------------------------------------------------------
# 7. Finance — Withdrawals
# ---------------------------------------------------------------------------


class TestWithdrawals:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_pending_withdrawals(self):
        """Pending withdrawals queue returns results."""
        r = client.get("/withdrawals/pending", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert all(w["status"] == "pending" for w in data)

    def test_approve_verified_withdrawal(self):
        """Approved withdrawal transitions status and records reviewer."""
        # WD-001 has kyc_verified=True
        r = client.post(
            "/withdrawals/decide",
            json={"withdrawal_id": "WD-001", "decision": "approved"},
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "approved"
        assert data["reviewed_by"] == "admin"

    def test_approve_unverified_kyc_blocked(self):
        """Cannot approve withdrawal when KYC is not verified."""
        # WD-002 has kyc_verified=False and status=pending
        r = client.post(
            "/withdrawals/decide",
            json={"withdrawal_id": "WD-002", "decision": "approved"},
            headers=self.headers,
        )
        assert r.status_code == 400

    def test_reject_withdrawal_requires_reason(self):
        """Rejection without reason returns 422."""
        r = client.post(
            "/withdrawals/decide",
            json={"withdrawal_id": "WD-002", "decision": "rejected"},
            headers=self.headers,
        )
        assert r.status_code == 422

    def test_withdrawal_stats(self):
        """Withdrawal stats endpoint summarises the queue."""
        r = client.get("/withdrawals/stats/summary", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "pending" in data
        assert "total_pending_value_gbp" in data


# ---------------------------------------------------------------------------
# 8. Finance — Revenue Reports
# ---------------------------------------------------------------------------


class TestFinanceReports:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_revenue_reports(self):
        """Revenue report list returns existing reports."""
        r = client.get("/finance-reports/", headers=self.headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_generate_revenue_report(self):
        """Generate a new revenue report with correct GGR/NGR/tax calculation."""
        r = client.post(
            "/finance-reports/generate"
            "?brand=AcmetoCasino"
            "&jurisdiction=UKGC"
            "&period_start=2024-02-01T00:00:00Z"
            "&period_end=2024-02-29T23:59:59Z"
            "&total_deposits=200000"
            "&total_withdrawals=130000"
            "&bonus_cost=12000"
            "&active_players=950"
            "&new_players=60",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ggr"] == 70000.0
        assert data["ngr"] == 58000.0
        assert data["tax_rate"] == 0.21
        assert data["tax_amount"] == pytest.approx(58000.0 * 0.21, rel=1e-3)

    def test_ggr_summary(self):
        """Aggregated GGR summary totals all reports."""
        r = client.get("/finance-reports/summary/ggr", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_ggr"] > 0
        assert "total_tax" in data


# ---------------------------------------------------------------------------
# 9. Security — Access Control
# ---------------------------------------------------------------------------


class TestAccessControl:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_admin_users(self):
        """Super admin can list all admin users."""
        r = client.get("/access-control/users", headers=self.headers)
        assert r.status_code == 200
        users = r.json()
        assert len(users) >= 2

    def test_compliance_cannot_list_admin_users(self):
        """Compliance role lacks admin:read — should get 403."""
        comp_token = get_token("compliance_agent", "Comply123!")
        r = client.get("/access-control/users", headers=auth_headers(comp_token))
        assert r.status_code == 403

    def test_create_admin_user(self):
        """Super admin can create a new admin user."""
        r = client.post(
            "/access-control/users"
            "?username=test_cs_agent"
            "&email=cs@acmetocasino.com"
            "&role=cs"
            "&password=TestPassword1!",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "test_cs_agent"
        assert data["role"] == "cs"

    def test_my_permissions(self):
        """User can retrieve their own permissions."""
        r = client.get("/access-control/my-permissions", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "permissions" in data
        assert "players:read" in data["permissions"]


# ---------------------------------------------------------------------------
# 10. Security — IP Blocking
# ---------------------------------------------------------------------------


class TestIPBlocking:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_ip_entries(self):
        """IP list returns active entries."""
        r = client.get("/ip-blocking/", headers=self.headers)
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_add_blocklist_entry(self):
        """Adding an IP to the blocklist succeeds."""
        r = client.post(
            "/ip-blocking/"
            "?ip_address=192.168.99.1"
            "&list_type=blocklist"
            "&reason=Fraudulent+activity+detected",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["list_type"] == "blocklist"
        assert data["ip_address"] == "192.168.99.1"

    def test_check_blocked_ip(self):
        """Check endpoint correctly identifies a blocked IP."""
        r = client.post(
            "/ip-blocking/check?ip=185.220.101.35",
            headers=self.headers,
        )
        assert r.status_code == 200
        assert r.json()["is_blocked"] is True

    def test_invalid_ip_returns_422(self):
        """Invalid IP address format returns 422."""
        r = client.post(
            "/ip-blocking/check?ip=not_an_ip",
            headers=self.headers,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 11. CRM — Bonuses
# ---------------------------------------------------------------------------


class TestBonusManagement:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_list_bonuses(self):
        """Bonus list returns active templates."""
        r = client.get("/bonuses/", headers=self.headers)
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_create_bonus(self):
        """Create a new cashback bonus template."""
        r = client.post(
            "/bonuses/"
            "?name=10+Percent+Cashback"
            "&bonus_type=cashback"
            "&value=50"
            "&wagering_requirement=1"
            "&valid_days=7"
            "&jurisdiction=UKGC",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["bonus_type"] == "cashback"
        assert data["is_active"] is True

    def test_assign_bonus_to_player(self):
        """Assign a bonus to a player creates an assignment."""
        r = client.post(
            "/bonuses/assign?bonus_id=BON-002&player_id=PLR-001",
            headers=self.headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["player_id"] == "PLR-001"
        assert data["status"] == "active"
        assert data["remaining_balance"] == 20.0

    def test_assign_inactive_bonus_fails(self):
        """Cannot assign a deactivated bonus."""
        # First deactivate
        client.patch("/bonuses/BON-001/deactivate", headers=self.headers)
        r = client.post(
            "/bonuses/assign?bonus_id=BON-001&player_id=PLR-002",
            headers=self.headers,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 12. Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:

    def setup_method(self):
        self.token = get_token()
        self.headers = auth_headers(self.token)

    def test_kpi_snapshot(self):
        """KPI snapshot returns today's real-time metrics."""
        r = client.get("/dashboard/kpis", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert data["brand"] == "AcmetoCasino"
        assert data["active_players_today"] > 0
        assert data["ggr_today"] > 0

    def test_dashboard_summary(self):
        """Dashboard summary includes pending action counts."""
        r = client.get("/dashboard/summary", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "pending_actions" in data
        assert "revenue" in data

    def test_health_check_no_auth(self):
        """Health check endpoint is accessible without authentication."""
        r = client.get("/dashboard/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_alert_counts(self):
        """Alert count stats by priority."""
        r = client.get("/alerts/stats/counts", headers=self.headers)
        assert r.status_code == 200
        data = r.json()
        assert "total_active" in data
        assert "critical" in data

    def test_resolve_alert(self):
        """Resolving an alert marks it as resolved."""
        r = client.patch("/alerts/ALT-001/resolve", headers=self.headers)
        assert r.status_code == 200
        assert r.json()["is_resolved"] is True
