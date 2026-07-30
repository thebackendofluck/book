#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 35b, Cash-Flow Integrity Incident Response.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""regulator_notify.py — draft a preliminary regulator notification for a cashflow incident.

Does NOT send. Drafts to stdout (or --out file) for the compliance team to review,
fill any gaps, and submit through the regulator's portal/email.

Templates per jurisdiction (UKGC, MGA, DGE, AGCO, Spelinspektionen, SIGAP, Curaçao, Gibraltar).

Usage:
  regulator_notify.py --jurisdiction UKGC \
                      --incident-id INC-2026-04-25-001 \
                      --detected-at "2026-04-25T22:15:00Z" \
                      --severity high \
                      --house-loss-eur 1790624 \
                      --rounds-affected 10105 \
                      --action-taken "kill_switch game=slots_pirate_bonanza" \
                      --out /tmp/regulator-draft.txt
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from textwrap import dedent

JURISDICTIONS = {
    "UKGC": {
        "name": "UK Gambling Commission",
        "sla_hours": 24,
        "ref": "LCCP 5.1.2 (Reporting key events)",
        "channel": "Key Event Return + email account manager",
    },
    "MGA": {
        "name": "Malta Gaming Authority",
        "sla_hours": 24,
        "ref": "Player Protection Directive Article 41",
        "channel": "Compliance reporting portal",
    },
    "DGE": {
        "name": "New Jersey Division of Gaming Enforcement",
        "sla_hours": 24,
        "ref": "NJAC 13:69O-1.4",
        "channel": "Form GG-31 + email DGE technical services",
    },
    "AGCO": {
        "name": "Alcohol and Gaming Commission of Ontario / iGO",
        "sla_hours": 24,
        "ref": "iGaming Ontario Standards 11.06",
        "channel": "Compliance portal (preliminary), 7-day full report",
    },
    "Spelinspektionen": {
        "name": "Spelinspektionen (Sweden)",
        "sla_hours": 24,
        "ref": "LIFS 2018:8",
        "channel": "Authority portal",
    },
    "SIGAP": {
        "name": "Secretaria de Prêmios e Apostas (Brasil)",
        "sla_hours": 24,
        "ref": "Portaria SPA/MF nº 1.231",
        "channel": "SIGAP portal + email SPA",
    },
    "Curacao": {
        "name": "Curaçao Gaming Control Board",
        "sla_hours": 72,
        "ref": "LOK Article 28",
        "channel": "Master licensee + GCB",
    },
    "Gibraltar": {
        "name": "Gibraltar Regulatory Authority",
        "sla_hours": 24,
        "ref": "LCCP-aligned",
        "channel": "Email regulator + risk register entry",
    },
}


def render(args: argparse.Namespace) -> str:
    j = JURISDICTIONS[args.jurisdiction]
    detected = datetime.fromisoformat(args.detected_at.replace("Z", "+00:00"))
    sla_deadline = detected.timestamp() + j["sla_hours"] * 3600
    sla_deadline_iso = datetime.fromtimestamp(sla_deadline, tz=detected.tzinfo).isoformat()
    return dedent(f"""\
        DRAFT REGULATORY NOTIFICATION — {j['name']}
        Reference: {j['ref']} (notification SLA: {j['sla_hours']}h)
        Channel: {j['channel']}

        Subject: Preliminary notification of operational incident — {args.incident_id}

        To the {j['name']}:

        We are writing to provide preliminary notification of an operational
        incident affecting our licensed gaming platform.

        Incident reference: {args.incident_id}
        Detected at:        {detected.isoformat()}
        SLA deadline:       {sla_deadline_iso}
        Severity:           {args.severity}
        Type:               Cash-flow integrity (RTP / house take anomaly)

        Summary
        -------
        At {detected.isoformat()}, our cash-flow integrity audit cron raised
        an alert indicating a sustained period during which the cumulative
        house take was negative and the rolling 24-hour Return To Player
        exceeded the published model. Total observed house loss in the
        affected window: EUR {args.house_loss_eur:,.2f} across
        {args.rounds_affected:,} game rounds.

        Immediate actions taken
        ----------------------
        - {args.action_taken}
        - Forensic snapshot of database, ledger, RNG seed audit, and application logs preserved.
        - Top-50 winning players in the window identified; withdrawals on those accounts placed under review.

        Investigation status
        --------------------
        Investigation is ongoing. Root cause has {'been preliminarily identified' if args.root_cause_known else 'not yet been determined'}.
        We will provide a full incident report and root-cause analysis within
        {7 if args.jurisdiction in ('AGCO',) else 14} days, in line with our license obligations.

        Player impact
        -------------
        Player balances are not affected. No player deposits or withdrawals
        already settled have been altered. Withdrawals from the top-50 affected
        accounts are paused pending forensic review and will be released or
        adjusted in accordance with our published terms and applicable law.

        Operator contact for this incident
        ---------------------------------
        Compliance officer of the day: <NAME>
        Email: <COMPLIANCE@OPERATOR>
        Phone: <+XX XX XXX XXXX>

        Yours sincerely,
        <DUTY OFFICER NAME>
        <ROLE>
        <OPERATOR LEGAL ENTITY>

        --- END DRAFT — REVIEW BEFORE SENDING ---
    """)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", required=True, choices=sorted(JURISDICTIONS))
    ap.add_argument("--incident-id", required=True)
    ap.add_argument("--detected-at", required=True, help="ISO 8601 UTC timestamp")
    ap.add_argument("--severity", required=True,
                    choices=("low", "medium", "high", "critical"))
    ap.add_argument("--house-loss-eur", type=float, required=True)
    ap.add_argument("--rounds-affected", type=int, required=True)
    ap.add_argument("--action-taken", required=True)
    ap.add_argument("--root-cause-known", action="store_true")
    ap.add_argument("--out", help="Write to file instead of stdout")
    args = ap.parse_args()

    text = render(args)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"draft written: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
