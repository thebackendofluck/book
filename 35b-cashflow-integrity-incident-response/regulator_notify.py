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

"""regulator_notify.py: draft a preliminary regulator notification for a cashflow incident.

Does NOT send. Drafts to stdout (or --out file) for the compliance liaison to
review, fill any gaps, and submit through the regulator's prescribed channel.
The compliance liaison owns the regulator notice (see Chapter 35b).

Deadlines follow the Regulatory Notification Matrix in Chapter 35b:

  UKGC  LCCP 15.2.1: as soon as reasonably practicable and no later than five
        working days after awareness. Book figure, confirm with counsel.
  MGA   Player Protection Directive, Article 37(2)(c)-(d): forthwith and no
        later than three working days. Book figure, confirm with counsel.
  Any other licence: there is NO universal 24-hour deadline. The deadline is
        "unknown, confirm with counsel". The script refuses to compute one
        unless you pass --confirmed-deadline-hours, a figure your regulatory
        counsel has confirmed for this licence and event type.

The GDPR 72 hour clock applies to personal data breaches only. It is not a
cash-flow incident deadline and is not used here.

Working days skip Saturday and Sunday only. Public holidays are not modeled,
so the computed date is an upper bound to check, not a legal determination.

Severity uses the Chapter 35 scale: SEV-1 (highest) to SEV-4 (lowest).

Usage:
  regulator_notify.py --jurisdiction UKGC \
                      --incident-id INC-2026-04-25-001 \
                      --detected-at "2026-04-25T22:15:00Z" \
                      --severity SEV-2 \
                      --house-loss-eur 1790624 \
                      --rounds-affected 10105 \
                      --action-taken "kill_switch game=slots_pirate_bonanza" \
                      --out /tmp/regulator-draft.txt
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from textwrap import dedent

UNKNOWN_DEADLINE = "unknown, confirm with counsel"
_OTHER_REF = (
    "Binding licence condition, regulation, technical standard, "
    "or regulator direction"
)
_OTHER_CHANNEL = "Current regulator portal or prescribed channel"

# sla_unit is "working_days" (book figure, confirm with counsel) or None when
# the deadline is unknown. sla_value is None whenever sla_unit is None.
JURISDICTIONS = {
    "UKGC": {
        "name": "UK Gambling Commission",
        "sla_unit": "working_days",
        "sla_value": 5,
        "sla_text": ("as soon as reasonably practicable and no later than five "
                     "working days after awareness (book figure, confirm with counsel)"),
        "ref": "LCCP 15.2.1",
        "channel": "eServices key-event report",
    },
    "MGA": {
        "name": "Malta Gaming Authority",
        "sla_unit": "working_days",
        "sla_value": 3,
        "sla_text": ("forthwith and no later than three working days "
                     "(book figure, confirm with counsel)"),
        "ref": "Player Protection Directive, Article 37(2)(c)-(d)",
        "channel": "MGA incident-reporting channel",
    },
    "DGE": {
        "name": "New Jersey Division of Gaming Enforcement",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
    "AGCO": {
        "name": "Alcohol and Gaming Commission of Ontario / iGO",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
    "Spelinspektionen": {
        "name": "Spelinspektionen (Sweden)",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
    "SIGAP": {
        "name": "Secretaria de Prêmios e Apostas (Brasil)",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
    "Curacao": {
        "name": "Curaçao Gaming Control Board",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
    "Gibraltar": {
        "name": "Gibraltar Regulatory Authority",
        "sla_unit": None, "sla_value": None, "sla_text": UNKNOWN_DEADLINE,
        "ref": _OTHER_REF, "channel": _OTHER_CHANNEL,
    },
}

SEVERITIES = ("SEV-1", "SEV-2", "SEV-3", "SEV-4")


class UnknownDeadlineError(ValueError):
    """Raised when no confirmed notification deadline exists for a jurisdiction."""


def add_working_days(start: datetime, days: int) -> datetime:
    """Add working days (Mon-Fri) to start. Public holidays are not modeled."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def compute_deadline(jurisdiction: str, detected: datetime,
                     confirmed_hours: float | None = None) -> datetime:
    """Return the latest notification time, or raise UnknownDeadlineError."""
    j = JURISDICTIONS[jurisdiction]
    days = j["sla_value"]
    if j["sla_unit"] == "working_days" and isinstance(days, int):
        return add_working_days(detected, days)
    if confirmed_hours is not None:
        return detected + timedelta(hours=confirmed_hours)
    raise UnknownDeadlineError(
        f"{jurisdiction}: deadline is {UNKNOWN_DEADLINE}. There is no universal "
        "24-hour rule. Pass --confirmed-deadline-hours with a figure confirmed "
        "by regulatory counsel for this licence and event type."
    )


def render(args: argparse.Namespace) -> str:
    j = JURISDICTIONS[args.jurisdiction]
    detected = datetime.fromisoformat(args.detected_at.replace("Z", "+00:00"))
    confirmed = getattr(args, "confirmed_deadline_hours", None)
    deadline = compute_deadline(args.jurisdiction, detected, confirmed)
    if j["sla_unit"] is None:
        sla_text = f"{confirmed:g} hours, confirmed by counsel"
    else:
        sla_text = j["sla_text"]
    return dedent(f"""\
        DRAFT REGULATORY NOTIFICATION - {j['name']}
        Reference: {j['ref']} (notification deadline: {sla_text})
        Channel: {j['channel']}

        Subject: Preliminary notification of operational incident - {args.incident_id}

        To the {j['name']}:

        We are writing to provide preliminary notification of an operational
        incident affecting our licensed gaming platform.

        Incident reference: {args.incident_id}
        Detected at:        {detected.isoformat()}
        Latest notice by:   {deadline.isoformat()} (check against counsel's advice)
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
        the timeline set by our licence conditions or the regulator.

        Player impact
        -------------
        Player balances are not affected. No player deposits or withdrawals
        already settled have been altered. Withdrawals from the top-50 affected
        accounts are paused pending forensic review and will be released or
        adjusted in accordance with our published terms and applicable law.

        Operator contact for this incident
        ---------------------------------
        Compliance liaison: <NAME>
        Email: <COMPLIANCE@OPERATOR>
        Phone: <+XX XX XXX XXXX>

        Yours sincerely,
        <COMPLIANCE LIAISON NAME>
        <ROLE>
        <OPERATOR LEGAL ENTITY>

        --- END DRAFT, REVIEW BEFORE SENDING ---
    """)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", required=True, choices=sorted(JURISDICTIONS))
    ap.add_argument("--incident-id", required=True)
    ap.add_argument("--detected-at", required=True, help="ISO 8601 UTC timestamp")
    ap.add_argument("--severity", required=True,
                    choices=SEVERITIES)
    ap.add_argument("--house-loss-eur", type=float, required=True)
    ap.add_argument("--rounds-affected", type=int, required=True)
    ap.add_argument("--action-taken", required=True)
    ap.add_argument("--confirmed-deadline-hours", type=float,
                    help="Deadline in hours confirmed by counsel; required for "
                         "jurisdictions whose deadline is unknown")
    ap.add_argument("--root-cause-known", action="store_true")
    ap.add_argument("--out", help="Write to file instead of stdout")
    args = ap.parse_args()

    try:
        text = render(args)
    except UnknownDeadlineError as exc:
        ap.error(str(exc))
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"draft written: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
