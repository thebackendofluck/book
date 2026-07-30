#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
cf-access-policies.py
Create and manage Cloudflare Access policies for an iGaming platform.
Each service gets a policy appropriate to its sensitivity level.

Usage: python3 cf-access-policies.py [--dry-run]

Environment variables:
    CF_API_TOKEN       - Cloudflare API token with Access:Edit permission
    CF_ACCOUNT_ID      - Cloudflare account ID
    CF_DOMAIN          - Base domain (default: acmetocasino.com)

Chapter 23 — DevSecOps for iGaming
"""

import json
import os
import sys
from typing import cast
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_DOMAIN = os.environ.get("CF_DOMAIN", "acmetocasino.com")
API_BASE = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/access"

DRY_RUN = "--dry-run" in sys.argv


def cf_api(method: str, path: str, data: dict | None = None) -> dict:
    """Make an authenticated request to the Cloudflare API."""
    url = f"{API_BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {CF_API_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode()
        print(f"API error: {e.code} {error_body}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Policy definitions for each iGaming service
# -----------------------------------------------------------------------
POLICIES = [
    {
        "name": "Backoffice Admin Panel",
        "hostname": f"admin.{CF_DOMAIN}",
        "decision": "allow",
        "session_duration": "4h",
        "purpose_justification_required": True,
        "include": [
            {"email_domain": {"domain": CF_DOMAIN}},
        ],
        "require": [
            # Require SAML group membership
            {"saml": {"attribute_name": "groups", "attribute_value": "backoffice-access"}},
            # Require hardware key (FIDO2)
            {"auth_method": {"auth_method": "hwk"}},
        ],
        "exclude": [
            # Block sanctioned jurisdictions
            {"geo": {"country_code": "KP"}},
            {"geo": {"country_code": "IR"}},
            {"geo": {"country_code": "CU"}},
            {"geo": {"country_code": "SY"}},
        ],
    },
    {
        "name": "Monitoring - Grafana",
        "hostname": f"grafana.{CF_DOMAIN}",
        "decision": "allow",
        "session_duration": "8h",
        "purpose_justification_required": False,
        "include": [
            {"email_domain": {"domain": CF_DOMAIN}},
        ],
        "require": [
            # Require SAML authentication (any 2FA method accepted)
            {"saml": {"attribute_name": "groups", "attribute_value": "monitoring-access"}},
        ],
        "exclude": [],
    },
    {
        "name": "Monitoring - Prometheus",
        "hostname": f"prometheus.{CF_DOMAIN}",
        "decision": "allow",
        "session_duration": "8h",
        "purpose_justification_required": False,
        "include": [
            {"email_domain": {"domain": CF_DOMAIN}},
        ],
        "require": [
            {"saml": {"attribute_name": "groups", "attribute_value": "monitoring-access"}},
        ],
        "exclude": [],
    },
    {
        "name": "Database Admin - pgAdmin",
        "hostname": f"dbadmin.{CF_DOMAIN}",
        "decision": "allow",
        "session_duration": "2h",  # Short session for sensitive access
        "purpose_justification_required": True,
        "include": [
            # Only specific DBA email addresses
            {"email": {"email": "eve@acmetocasino.com"}},
            {"email": {"email": "carol@acmetocasino.com"}},
        ],
        "require": [
            # Require hardware key AND SAML
            {"auth_method": {"auth_method": "hwk"}},
            {"saml": {"attribute_name": "groups", "attribute_value": "dba-team"}},
        ],
        "exclude": [
            {"geo": {"country_code": "KP"}},
            {"geo": {"country_code": "IR"}},
        ],
    },
    {
        "name": "CI/CD - Jenkins",
        "hostname": f"ci.{CF_DOMAIN}",
        "decision": "allow",
        "session_duration": "8h",
        "purpose_justification_required": False,
        "include": [
            {"saml": {"attribute_name": "groups", "attribute_value": "engineering"}},
            {"saml": {"attribute_name": "groups", "attribute_value": "operations"}},
        ],
        "require": [],
        "exclude": [],
    },
    {
        "name": "API Documentation",
        "hostname": f"docs.{CF_DOMAIN}",
        "decision": "bypass",  # Public access -- no auth required
        "session_duration": None,
        "purpose_justification_required": False,
        "include": [{"everyone": {}}],
        "require": [],
        "exclude": [],
    },
]


def create_application(policy: dict) -> dict:
    """Create a Cloudflare Access application."""
    app_data = {
        "name": policy["name"],
        "domain": policy["hostname"],
        "type": "self_hosted",
        "session_duration": policy.get("session_duration", "24h"),
        "auto_redirect_to_identity": True,
        "http_only_cookie_attribute": True,
        "same_site_cookie_attribute": "lax",
        "skip_interstitial": True,
    }

    if policy.get("purpose_justification_required"):
        app_data["purpose_justification_required"] = True
        app_data["purpose_justification_prompt"] = (
            "Explain why you need access to this service. "
            "This will be logged for compliance auditing."
        )

    return app_data


def create_policy_rules(policy: dict, app_id: str) -> dict:
    """Create Access policy rules for an application."""
    rule_data = {
        "name": f"{policy['name']} Policy",
        "decision": policy["decision"],
        "include": policy.get("include", []),
        "require": policy.get("require", []),
        "exclude": policy.get("exclude", []),
    }
    return rule_data


def main():
    if not CF_API_TOKEN or not CF_ACCOUNT_ID:
        print("ERROR: Set CF_API_TOKEN and CF_ACCOUNT_ID", file=sys.stderr)
        sys.exit(1)

    print(f"Configuring Cloudflare Access for {CF_DOMAIN}")
    print(f"{'DRY RUN -- no changes will be made' if DRY_RUN else 'LIVE -- creating policies'}")
    print("=" * 60)

    for policy in POLICIES:
        print(f"\n--- {policy['name']} ({policy['hostname']}) ---")
        print(f"  Decision: {policy['decision']}")
        print(f"  Session duration: {policy.get('session_duration', 'N/A')}")
        print(f"  Purpose justification: {policy.get('purpose_justification_required', False)}")
        print(f"  Include rules: {len(cast(list, policy.get('include', [])))}")
        print(f"  Require rules: {len(cast(list, policy.get('require', [])))}")
        print(f"  Exclude rules: {len(cast(list, policy.get('exclude', [])))}")

        if DRY_RUN:
            print("  [DRY RUN] Would create application and policy")
            continue

        # Create the Access Application
        app_data = create_application(policy)
        result = cf_api("POST", "/apps", app_data)

        if not result.get("success"):
            print(f"  ERROR: {result.get('errors', 'Unknown error')}")
            continue

        app_id = result["result"]["id"]
        print(f"  Application created: {app_id}")

        # Create the policy (skip for bypass decisions)
        if policy["decision"] != "bypass":
            rule_data = create_policy_rules(policy, app_id)
            policy_result = cf_api("POST", f"/apps/{app_id}/policies", rule_data)

            if policy_result.get("success"):
                policy_id = policy_result["result"]["id"]
                print(f"  Policy created: {policy_id}")
            else:
                print(f"  Policy ERROR: {policy_result.get('errors', 'Unknown error')}")

    print("\n" + "=" * 60)
    print("Access policy configuration complete.")
    if not DRY_RUN:
        print(f"Verify at: https://one.dash.cloudflare.com/{CF_ACCOUNT_ID}/access/apps")


if __name__ == "__main__":
    main()
