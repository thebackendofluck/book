#!/usr/bin/env python3
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
Auto-Generated Communication Templates for iGaming Incident Response.

Generates context-aware communications for:
- Internal stakeholders (Slack, email)
- External players (email, in-app, SMS)
- Regulatory bodies (formal notifications)
- Payment providers and game suppliers

Usage:
    python communication_templates.py internal --severity P1 --title "Platform outage" --status investigating
    python communication_templates.py player --severity P2 --title "Deposit delays" --status resolved
    python communication_templates.py regulatory --severity P1 --title "Data breach" --jurisdiction UKGC
    python communication_templates.py status-page --severity P1 --title "Service disruption" --status investigating
"""

import argparse
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CommChannel(Enum):
    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"
    STATUS_PAGE = "status_page"
    REGULATORY = "regulatory"


# ---------------------------------------------------------------------------
# Internal Communication Templates
# ---------------------------------------------------------------------------

INTERNAL_TEMPLATES = {
    "declared": {
        "slack": """
:rotating_light: *INCIDENT DECLARED - {severity}* :rotating_light:

*Title:* {title}
*Severity:* {severity}
*Category:* {category}
*Incident ID:* {incident_id}
*Declared by:* {declared_by}
*Time:* {timestamp}

*Affected Services:* {affected_services}
*Affected Jurisdictions:* {affected_jurisdictions}

*Incident Commander:* {incident_commander}
*War Room:* {war_room_link}

*Current Status:* Investigating - initial assessment in progress.

*Next Update:* {next_update_time}

cc: {escalation_contacts}
""",
        "email_subject": "[{severity}] INCIDENT: {title} - {incident_id}",
        "email_body": """
INCIDENT NOTIFICATION

Incident ID: {incident_id}
Title: {title}
Severity: {severity}
Category: {category}
Declared: {timestamp}

Affected Services: {affected_services}
Affected Jurisdictions: {affected_jurisdictions}

Current Status: Under investigation

Incident Commander: {incident_commander}
War Room: {war_room_link}

We will provide updates every {update_frequency} minutes.

Please join the war room if you are on the escalation list.
""",
    },
    "investigating": {
        "slack": """
:mag: *INCIDENT UPDATE - {severity} - {incident_id}*

*Title:* {title}
*Status:* :yellow_circle: Investigating
*Duration:* {duration}

*Update:*
{update_details}

*What we know:*
- {known_facts}

*What we're doing:*
- {current_actions}

*Next Update:* {next_update_time}
""",
    },
    "identified": {
        "slack": """
:dart: *INCIDENT UPDATE - {severity} - {incident_id}*

*Title:* {title}
*Status:* :large_blue_circle: Root Cause Identified
*Duration:* {duration}

*Root Cause:*
{root_cause}

*Mitigation Plan:*
{mitigation_plan}

*ETA to Resolution:* {eta}
*Next Update:* {next_update_time}
""",
    },
    "resolved": {
        "slack": """
:white_check_mark: *INCIDENT RESOLVED - {severity} - {incident_id}*

*Title:* {title}
*Duration:* {duration}
*Resolved at:* {resolved_at}

*Root Cause:* {root_cause}
*Resolution:* {resolution}

*Impact:*
- Players affected: ~{players_affected}
- Revenue impact: ~EUR {revenue_impact}

*Post-mortem scheduled:* {postmortem_date}

*Action items:*
{action_items}
""",
        "email_subject": "[RESOLVED] {title} - {incident_id}",
        "email_body": """
INCIDENT RESOLVED

Incident ID: {incident_id}
Title: {title}
Duration: {duration}
Resolution Time: {resolved_at}

Root Cause: {root_cause}
Resolution: {resolution}

Impact Summary:
- Estimated players affected: {players_affected}
- Estimated revenue impact: EUR {revenue_impact}

Post-mortem has been scheduled for {postmortem_date}.

Thank you for your response and support during this incident.
""",
    },
}


# ---------------------------------------------------------------------------
# Player Communication Templates
# ---------------------------------------------------------------------------

PLAYER_TEMPLATES = {
    "investigating": {
        "email_subject": "Service Update - We're Looking Into It",
        "email_body": """
Dear {player_name},

We're currently experiencing {issue_summary}. Our technical team is actively investigating and working to resolve this as quickly as possible.

What this means for you:
{player_impact_description}

What you can do:
{player_actions}

We apologize for any inconvenience and will keep you updated on our progress.

If you have any questions, please contact our support team at {support_email} or via live chat.

Best regards,
{operator_name} Team
""",
        "sms": "Hi {player_name}, we're aware of {issue_summary_short} and working on a fix. We'll update you soon. Questions? Contact {support_short}.",
        "in_app": """We're currently experiencing {issue_summary_short}. Our team is working on it. We apologize for the inconvenience.""",
    },
    "resolved": {
        "email_subject": "Service Restored - Thank You for Your Patience",
        "email_body": """
Dear {player_name},

We're pleased to let you know that {issue_summary} has been fully resolved.

Duration: {duration}

What happened:
{root_cause_simplified}

If you experienced any issues during this time:
{compensation_details}

All services are now operating normally. If you continue to experience any problems, please don't hesitate to reach out to our support team.

We sincerely apologize for the inconvenience and thank you for your patience.

Best regards,
{operator_name} Team
""",
        "sms": "Good news, {player_name}! {issue_summary_short} is now resolved. {compensation_short} Sorry for any trouble! -{operator_name}",
        "in_app": """All services have been restored. We apologize for the disruption. {compensation_short}""",
    },
    "compensation": {
        "email_subject": "A Token of Our Appreciation",
        "email_body": """
Dear {player_name},

Thank you for your patience during the recent {issue_summary}.

As a token of our appreciation, we've credited your account with:
{compensation_items}

These bonuses are available in your account now and are valid for {validity_period}.

Terms and conditions:
{bonus_terms}

Thank you for being a valued member of {operator_name}.

Best regards,
{operator_name} Team
""",
    },
}


# ---------------------------------------------------------------------------
# Regulatory Communication Templates
# ---------------------------------------------------------------------------

REGULATORY_TEMPLATES = {
    "UKGC": {
        "subject": "Regulatory Notification - Key Event - {incident_id}",
        "body": """
UK Gambling Commission
Key Event Notification

Operator: {operator_name}
License Number: {license_number}
Date of Event: {event_date}
Date of Notification: {notification_date}

1. NATURE OF EVENT
{event_description}

2. CATEGORY
{event_category}

3. IMPACT ASSESSMENT
   a) Number of consumers affected: {players_affected}
   b) Financial impact: GBP {financial_impact_gbp}
   c) Duration of impact: {duration}
   d) Geographic scope: United Kingdom

4. ACTIONS TAKEN
{actions_taken}

5. ROOT CAUSE (if known)
{root_cause}

6. PREVENTIVE MEASURES
{preventive_measures}

7. CONTACT FOR FURTHER INFORMATION
   Name: {contact_name}
   Role: {contact_role}
   Email: {contact_email}
   Phone: {contact_phone}

This notification is submitted in accordance with Licence Condition 15.2.1.

Signed: {signatory_name}
Role: {signatory_role}
Date: {notification_date}
""",
    },
    "MGA": {
        "subject": "MGA Notification - Reportable Event - {incident_id}",
        "body": """
Malta Gaming Authority
Reportable Event Notification

Licensee: {operator_name}
License Number: {license_number}
Event Date: {event_date}
Notification Date: {notification_date}

SECTION 1: EVENT DETAILS
Type: {event_category}
Description: {event_description}

SECTION 2: IMPACT
Players affected: {players_affected}
Financial impact: EUR {financial_impact_eur}
Duration: {duration}
Jurisdictions: Malta, {other_jurisdictions}

SECTION 3: IMMEDIATE ACTIONS
{actions_taken}

SECTION 4: ROOT CAUSE ANALYSIS
{root_cause}

SECTION 5: CORRECTIVE & PREVENTIVE ACTIONS
{preventive_measures}

SECTION 6: CONTACT
{contact_name} - {contact_role}
{contact_email} | {contact_phone}

Submitted pursuant to MGA Directive 2 of 2018.
""",
    },
    "Curacao": {
        "subject": "Incident Notification - {incident_id}",
        "body": """
Curacao Gaming Control Board
Incident Report

Operator: {operator_name}
License: {license_number}
Incident Date: {event_date}

Description: {event_description}
Category: {event_category}
Impact: {players_affected} players, USD {financial_impact_usd}
Duration: {duration}

Actions Taken: {actions_taken}
Root Cause: {root_cause}

Contact: {contact_name}, {contact_email}
""",
    },
}


# ---------------------------------------------------------------------------
# Status Page Templates
# ---------------------------------------------------------------------------

STATUS_PAGE_TEMPLATES = {
    "investigating": {
        "title": "Investigating - {title}",
        "body": "We are currently investigating {issue_summary_short}. We will provide updates as more information becomes available.",
        "component_status": "major_outage" if "{severity}" == "P1" else "partial_outage",
    },
    "identified": {
        "title": "Identified - {title}",
        "body": "The root cause of {issue_summary_short} has been identified. Our engineering team is implementing a fix. ETA: {eta}.",
        "component_status": "partial_outage",
    },
    "monitoring": {
        "title": "Monitoring - {title}",
        "body": "A fix has been implemented for {issue_summary_short}. We are monitoring the situation to ensure stability.",
        "component_status": "degraded_performance",
    },
    "resolved": {
        "title": "Resolved - {title}",
        "body": "The issue with {issue_summary_short} has been resolved. All systems are operational. We apologize for the inconvenience.",
        "component_status": "operational",
    },
}


# ---------------------------------------------------------------------------
# Template Generator
# ---------------------------------------------------------------------------

class CommunicationGenerator:
    """Generate context-aware communications from templates."""

    def __init__(self):
        self.defaults = {
            "operator_name": "AcmetoCasino",
            "support_email": "support@acmetocasino.com",
            "support_short": "support@acmetocasino.com",
            "war_room_link": "https://meet.casino.internal/war-room",
            "update_frequency": "15",
            "license_number": "[LICENSE_NUMBER]",
            "contact_name": "[COMPLIANCE_OFFICER]",
            "contact_role": "Head of Compliance",
            "contact_email": "[compliance@acmetocasino.com]",
            "contact_phone": "[+356-XXXX-XXXX]",
            "signatory_name": "[SIGNATORY]",
            "signatory_role": "[ROLE]",
            "postmortem_date": "[TBD]",
            "eta": "[TBD]",
            "bonus_terms": "Standard wagering requirements apply. See full T&Cs.",
            "validity_period": "7 days",
        }

    def generate_internal(self, severity: str, title: str, status: str, **kwargs) -> dict:
        """Generate internal communications (Slack + email)."""
        params = {**self.defaults, **kwargs}
        params.update({
            "severity": severity,
            "title": title,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "incident_id": kwargs.get("incident_id", f"INC-{datetime.now(timezone.utc).strftime('%Y')}-DRAFT"),
            "next_update_time": f"~{int(params.get('update_frequency', 15))} minutes",
        })

        # Fill defaults for missing fields
        for field in ["category", "declared_by", "incident_commander", "affected_services",
                      "affected_jurisdictions", "escalation_contacts", "duration",
                      "update_details", "known_facts", "current_actions", "root_cause",
                      "mitigation_plan", "resolution", "players_affected", "revenue_impact",
                      "action_items", "resolved_at"]:
            params.setdefault(field, "[TBD]")

        templates = INTERNAL_TEMPLATES.get(status, INTERNAL_TEMPLATES["declared"])
        results = {}

        for channel, template in templates.items():
            try:
                results[channel] = template.format(**params)
            except KeyError as e:
                results[channel] = f"[Template error: missing field {e}]\n{template}"

        return results

    def generate_player(self, severity: str, title: str, status: str, **kwargs) -> dict:
        """Generate player-facing communications."""
        params = {**self.defaults, **kwargs}
        params.update({
            "severity": severity,
            "title": title,
            "player_name": kwargs.get("player_name", "Valued Player"),
        })

        # Map severity to player-facing language
        impact_map = {
            "P1": {
                "issue_summary": "a temporary service disruption affecting our platform",
                "issue_summary_short": "a temporary service issue",
                "player_impact_description": "- You may be unable to access your account or place bets\n- Any in-progress games have been saved\n- Your balance and pending withdrawals are safe",
                "player_actions": "- Please wait and try again shortly\n- Do not re-submit any pending transactions\n- Contact support if you have urgent concerns",
                "root_cause_simplified": "A technical issue in our infrastructure caused a temporary service interruption.",
            },
            "P2": {
                "issue_summary": "some players may experience delays with certain features",
                "issue_summary_short": "temporary delays",
                "player_impact_description": "- Some features may be temporarily unavailable\n- Deposits and withdrawals may experience delays\n- Your account balance is unaffected",
                "player_actions": "- Most features remain available\n- Please wait a few minutes before retrying any failed actions\n- Contact support if issues persist",
                "root_cause_simplified": "A technical issue caused temporary delays in some of our services.",
            },
        }
        defaults_for_sev = impact_map.get(severity, impact_map["P2"])
        for k, v in defaults_for_sev.items():
            params.setdefault(k, v)

        # Compensation defaults
        params.setdefault("compensation_details", "- If you had an active bonus, its timer has been paused\n- If you experienced a failed deposit, it will be automatically retried\n- Contact support for any unresolved issues")
        params.setdefault("compensation_short", "Check your account for a goodwill bonus.")
        params.setdefault("compensation_items", "- EUR 5 Free Bet\n- 10 Free Spins on selected slots")

        templates = PLAYER_TEMPLATES.get(status, PLAYER_TEMPLATES["investigating"])
        results = {}

        for channel, template in templates.items():
            try:
                results[channel] = template.format(**params)
            except KeyError as e:
                results[channel] = f"[Template error: missing field {e}]"

        return results

    def generate_regulatory(self, severity: str, title: str, jurisdiction: str, **kwargs) -> dict:
        """Generate regulatory notification."""
        params = {**self.defaults, **kwargs}
        params.update({
            "severity": severity,
            "title": title,
            "event_date": kwargs.get("event_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "notification_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "incident_id": kwargs.get("incident_id", f"INC-{datetime.now(timezone.utc).strftime('%Y')}-DRAFT"),
        })

        for field in ["event_description", "event_category", "players_affected",
                      "financial_impact_gbp", "financial_impact_eur", "financial_impact_usd",
                      "duration", "actions_taken", "root_cause", "preventive_measures",
                      "other_jurisdictions"]:
            params.setdefault(field, "[TO BE COMPLETED]")

        template_set = REGULATORY_TEMPLATES.get(jurisdiction)
        if not template_set:
            return {"error": f"No template for jurisdiction: {jurisdiction}. "
                             f"Available: {list(REGULATORY_TEMPLATES.keys())}"}

        results = {}
        for key, template in template_set.items():
            try:
                results[key] = template.format(**params)
            except KeyError as e:
                results[key] = f"[Template error: missing field {e}]"

        return results

    def generate_status_page(self, severity: str, title: str, status: str, **kwargs) -> dict:
        """Generate status page update."""
        params = {
            "severity": severity,
            "title": title,
            "issue_summary_short": kwargs.get("issue_summary_short", title.lower()),
            "eta": kwargs.get("eta", "[TBD]"),
        }

        template = STATUS_PAGE_TEMPLATES.get(status, STATUS_PAGE_TEMPLATES["investigating"])
        results = {}
        for key, value in template.items():
            try:
                results[key] = value.format(**params) if isinstance(value, str) else value
            except KeyError:
                results[key] = value

        return results


def print_results(results: dict, channel_type: str):
    """Pretty-print generated communications."""
    print(f"\n{'='*70}")
    print(f"GENERATED {channel_type.upper()} COMMUNICATIONS")
    print(f"{'='*70}")
    for channel, content in results.items():
        print(f"\n--- {channel.upper()} ---")
        print(content)
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="iGaming Incident Communication Template Generator")
    subparsers = parser.add_subparsers(dest="target")

    # Common args
    for name in ["internal", "player", "regulatory", "status-page"]:
        sub = subparsers.add_parser(name)
        sub.add_argument("--severity", required=True, choices=["P1", "P2", "P3", "P4"])
        sub.add_argument("--title", required=True)
        sub.add_argument("--status", default="investigating",
                         choices=["declared", "investigating", "identified", "resolved", "monitoring"])
        sub.add_argument("--incident-id", default=None)
        if name == "regulatory":
            sub.add_argument("--jurisdiction", required=True, choices=list(REGULATORY_TEMPLATES.keys()))

    # Demo
    subparsers.add_parser("demo", help="Generate demo communications for all channels")

    args = parser.parse_args()
    gen = CommunicationGenerator()

    if args.target == "internal":
        results = gen.generate_internal(args.severity, args.title, args.status,
                                        incident_id=args.incident_id)
        print_results(results, "internal")

    elif args.target == "player":
        results = gen.generate_player(args.severity, args.title, args.status)
        print_results(results, "player")

    elif args.target == "regulatory":
        results = gen.generate_regulatory(args.severity, args.title, args.jurisdiction,
                                          incident_id=args.incident_id)
        print_results(results, f"regulatory ({args.jurisdiction})")

    elif args.target == "status-page":
        results = gen.generate_status_page(args.severity, args.title, args.status)
        print_results(results, "status page")

    elif args.target == "demo":
        print("\n" + "="*70)
        print("DEMO: Full Incident Communication Suite")
        print("="*70)

        print("\n--- P1 Payment Gateway Failure ---\n")
        r = gen.generate_internal("P1", "Payment gateway timeout - all deposits failing",
                                  "declared", category="payment_failure",
                                  affected_services="payment-service, deposit-api",
                                  affected_jurisdictions="UKGC, MGA")
        print_results(r, "internal")

        r = gen.generate_player("P1", "Payment gateway timeout", "investigating")
        print_results(r, "player")

        r = gen.generate_regulatory("P1", "Payment gateway timeout", "UKGC",
                                    event_description="Complete failure of deposit processing",
                                    event_category="Payment System Failure",
                                    players_affected="~25,000",
                                    financial_impact_gbp="~50,000")
        print_results(r, "regulatory (UKGC)")

        r = gen.generate_status_page("P1", "Deposit Processing", "investigating")
        print_results(r, "status page")

        print("\n--- Resolution Communications ---\n")
        r = gen.generate_player("P1", "Payment gateway timeout", "resolved",
                                root_cause_simplified="A configuration error in our payment provider caused temporary deposit failures.",
                                compensation_items="- EUR 10 Free Bet\n- 20 Free Spins on Book of Dead")
        print_results(r, "player (resolved)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
