#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Breach Notification Deadline Tracker
=====================================
Tracks data breach notification deadlines across multiple iGaming jurisdictions
and sends alerts when deadlines approach.

Jurisdictions covered:
- EU GDPR (Art. 33/34) — 72 hours to DPA
- UK GDPR / ICO — 72 hours
- Malta MGA + IDPC — 72 hours (GDPR) + prompt MGA notification
- NJ DGE — Prompt initial + 30 days written
- Brazil LGPD / ANPD — 3 business days (Resolução CD/ANPD nº 15/2024)
- Sweden IMY + Spelinspektionen — 72 hours
- Ontario OPC + AGCO — As soon as feasible (PIPEDA)
- Curacao CGA — Without delay
- Gibraltar GRA + Gibraltar DPA — 72 hours
- Netherlands AP + KSA — 72 hours

Usage:
    # Register a new breach
    python3 breach_notification_tracker.py register \
        --incident-id INC-2026-042 \
        --awareness-time "2026-04-03T03:15:00Z" \
        --jurisdictions EU,UK,MT,SE

    # Check current status
    python3 breach_notification_tracker.py status --incident-id INC-2026-042

    # Mark a notification as filed
    python3 breach_notification_tracker.py filed \
        --incident-id INC-2026-042 \
        --jurisdiction EU \
        --reference-number ICO-2026-000123

    # Run deadline monitor (call from cron every 15 minutes)
    python3 breach_notification_tracker.py monitor
"""

import json
import os
import smtplib
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Jurisdiction definitions
# ---------------------------------------------------------------------------

@dataclass
class NotificationRequirement:
    jurisdiction_code: str
    jurisdiction_name: str
    primary_regulator: str
    secondary_regulator: Optional[str]
    deadline_hours: Optional[float]        # None = "without delay" (treat as 24h)
    deadline_business_days: Optional[int]  # Used for LGPD (Brazil)
    player_notification_required: bool
    player_notification_trigger: str
    player_notification_window_hours: Optional[int]
    contact_url: str
    notes: str


JURISDICTION_REQUIREMENTS: dict[str, NotificationRequirement] = {
    "EU": NotificationRequirement(
        jurisdiction_code="EU",
        jurisdiction_name="EU (GDPR)",
        primary_regulator="National DPA (lead supervisory authority)",
        secondary_regulator=None,
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms (Art. 34)",
        player_notification_window_hours=None,  # "without undue delay"
        contact_url="https://edpb.europa.eu/about-edpb/about-edpb/members_en",
        notes=(
            "GDPR Art. 33(1). Clock starts at controller awareness, not breach occurrence. "
            "Art. 33(4) permits phased notification if full info unavailable. "
            "Max penalty for notification failure: EUR 10M or 2% global turnover."
        ),
    ),
    "UK": NotificationRequirement(
        jurisdiction_code="UK",
        jurisdiction_name="United Kingdom (UK GDPR)",
        primary_regulator="Information Commissioner's Office (ICO)",
        secondary_regulator="UK Gambling Commission (UKGC) — material incidents",
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms",
        player_notification_window_hours=None,
        contact_url="https://ico.org.uk/report",
        notes=(
            "UK GDPR post-Brexit mirrors GDPR Art. 33. ICO enforcement appetite is high in 2026. "
            "UKGC expects 'immediate' notification for material security incidents separately. "
            "Max penalty: £17.5M or 4% global turnover."
        ),
    ),
    "MT": NotificationRequirement(
        jurisdiction_code="MT",
        jurisdiction_name="Malta (MGA + GDPR)",
        primary_regulator="Malta Gaming Authority (MGA)",
        secondary_regulator="Information and Data Protection Commissioner (IDPC)",
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms",
        player_notification_window_hours=None,
        contact_url="https://www.mga.org.mt/compliance/",
        notes=(
            "MGA expects 'without delay' for critical incidents. IDPC handles GDPR obligations. "
            "March 2026 MGA system breach underscores regulator scrutiny. "
            "Penalties: License suspension + fines up to EUR 5M."
        ),
    ),
    "NJ": NotificationRequirement(
        jurisdiction_code="NJ",
        jurisdiction_name="New Jersey (DGE)",
        primary_regulator="NJ Division of Gaming Enforcement (DGE)",
        secondary_regulator="NJ Attorney General",
        deadline_hours=24,  # "prompt" — treat as 24h for tracking
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="Confirmed breach of NJ player personal data",
        player_notification_window_hours=720,  # 30 days per NJ consumer law
        contact_url="https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/",
        notes=(
            "DGE requires prompt initial notification; no exact hour specified. "
            "NJ Identity Theft Prevention Act requires written notice to affected players within 30 days. "
            "Annual security assessment required from independent testing professional."
        ),
    ),
    "BR": NotificationRequirement(
        jurisdiction_code="BR",
        jurisdiction_name="Brazil (LGPD / ANPD)",
        primary_regulator="Autoridade Nacional de Proteção de Dados (ANPD)",
        secondary_regulator=None,
        deadline_hours=None,
        deadline_business_days=3,
        player_notification_required=True,
        player_notification_trigger="Significant risk or relevant damage to data subjects",
        player_notification_window_hours=None,
        contact_url="https://www.gov.br/anpd/",
        notes=(
            "Resolução CD/ANPD nº 15/2024 (effective April 2024). "
            "3 business days from CONFIRMATION that incident affected personal data. "
            "Deadline doubled (6 business days) for small-sized processing agents. "
            "Supplemental info can be provided within 20 business days of initial filing. "
            "Penalties: up to 2% Brazil revenue, max R$50M per violation (LGPD Art. 48/52)."
        ),
    ),
    "SE": NotificationRequirement(
        jurisdiction_code="SE",
        jurisdiction_name="Sweden (GDPR + Spelinspektionen)",
        primary_regulator="Integritetsskyddsmyndigheten (IMY)",
        secondary_regulator="Spelinspektionen",
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms",
        player_notification_window_hours=None,
        contact_url="https://www.imy.se/en/",
        notes=(
            "IMY handles GDPR obligations. Spelinspektionen requires 'immediate' notification "
            "for incidents affecting Swedish licensees. IMY applies GDPR fines actively."
        ),
    ),
    "ON": NotificationRequirement(
        jurisdiction_code="ON",
        jurisdiction_name="Ontario, Canada (PIPEDA + AGCO)",
        primary_regulator="Office of the Privacy Commissioner (OPC)",
        secondary_regulator="Alcohol and Gaming Commission of Ontario (AGCO)",
        deadline_hours=None,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="Real risk of significant harm threshold (PIPEDA)",
        player_notification_window_hours=None,  # "as soon as feasible"
        contact_url="https://www.priv.gc.ca/",
        notes=(
            "PIPEDA's 'as soon as feasible' standard is interpreted as within 72 hours in practice. "
            "AGCO expects prompt notification under iGaming Ontario operating standards. "
            "Remedies: compliance orders + license sanctions."
        ),
    ),
    "CW": NotificationRequirement(
        jurisdiction_code="CW",
        jurisdiction_name="Curacao (CGA)",
        primary_regulator="Curacao Gaming Authority (CGA)",
        secondary_regulator=None,
        deadline_hours=24,  # "without delay" — treat as 24h
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="Any breach affecting player data",
        player_notification_window_hours=168,  # 7 days
        contact_url="https://www.gaming-curacao.com/",
        notes=(
            "Landsverordening op de Kansspelen (LOK) Art. 21 requires 'without delay' notification. "
            "New CGA framework (2023 overhaul) increased enforcement. Penalty: License revocation."
        ),
    ),
    "GI": NotificationRequirement(
        jurisdiction_code="GI",
        jurisdiction_name="Gibraltar (GRA + Gibraltar DPA)",
        primary_regulator="Gibraltar Regulatory Authority (GRA)",
        secondary_regulator="Gibraltar Data Protection Commissioner",
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms",
        player_notification_window_hours=None,
        contact_url="https://www.gra.gi/",
        notes=(
            "Gibraltar adopted GDPR-equivalent legislation post-Brexit. "
            "GRA operates a dual-notification requirement alongside the Data Protection Commissioner. "
            "Penalties: Fine + license review."
        ),
    ),
    "NL": NotificationRequirement(
        jurisdiction_code="NL",
        jurisdiction_name="Netherlands (AP + KSA)",
        primary_regulator="Autoriteit Persoonsgegevens (AP)",
        secondary_regulator="Kansspelautoriteit (KSA)",
        deadline_hours=72,
        deadline_business_days=None,
        player_notification_required=True,
        player_notification_trigger="High risk to rights and freedoms",
        player_notification_window_hours=None,
        contact_url="https://www.autoriteitpersoonsgegevens.nl/en",
        notes=(
            "AP handles GDPR obligations. KSA expects notification within 72 hours for incidents "
            "affecting Dutch licensees. KSA penalties up to EUR 900K; GDPR fines up to EUR 20M."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Breach incident tracking
# ---------------------------------------------------------------------------

@dataclass
class JurisdictionStatus:
    jurisdiction_code: str
    deadline_utc: Optional[str]  # ISO8601
    status: str  # pending | filed | overdue | not_required
    filed_at: Optional[str]
    reference_number: Optional[str]
    notes: str = ""


@dataclass
class BreachIncident:
    incident_id: str
    awareness_time_utc: str  # ISO8601
    created_at: str
    description: str
    jurisdictions: list[JurisdictionStatus] = field(default_factory=list)
    player_notification_required: bool = False
    player_notification_filed: bool = False
    player_notification_filed_at: Optional[str] = None
    status: str = "active"  # active | closed


TRACKER_FILE = Path(os.environ.get("BREACH_TRACKER_FILE", "/var/lib/breach_tracker/incidents.json"))


def _load_incidents() -> dict[str, BreachIncident]:
    if not TRACKER_FILE.exists():
        return {}
    data = json.loads(TRACKER_FILE.read_text())
    incidents = {}
    for iid, raw in data.items():
        jurisdictions = [JurisdictionStatus(**j) for j in raw.pop("jurisdictions", [])]
        incidents[iid] = BreachIncident(**raw, jurisdictions=jurisdictions)
    return incidents


def _save_incidents(incidents: dict[str, BreachIncident]) -> None:
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    for iid, incident in incidents.items():
        raw = asdict(incident)
        data[iid] = raw
    TRACKER_FILE.write_text(json.dumps(data, indent=2))


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _add_business_days(start: datetime, days: int) -> datetime:
    """Add N business days (Mon-Fri) to a datetime."""
    count = 0
    current = start
    while count < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            count += 1
    return current


def _compute_deadline(awareness: datetime, req: NotificationRequirement) -> Optional[datetime]:
    if req.deadline_hours is not None:
        return awareness + timedelta(hours=req.deadline_hours)
    elif req.deadline_business_days is not None:
        return _add_business_days(awareness, req.deadline_business_days)
    return None  # "as soon as feasible" — no hard deadline


def register_incident(
    incident_id: str,
    awareness_time: str,
    jurisdiction_codes: list[str],
    description: str = "",
) -> BreachIncident:
    incidents = _load_incidents()
    if incident_id in incidents:
        print(f"[!] Incident {incident_id} already registered.", file=sys.stderr)
        return incidents[incident_id]

    awareness = _parse_utc(awareness_time)
    now_utc = datetime.now(timezone.utc).isoformat()

    jurisdiction_statuses = []
    for code in jurisdiction_codes:
        req = JURISDICTION_REQUIREMENTS.get(code.upper())
        if req is None:
            print(f"[!] Unknown jurisdiction code: {code}", file=sys.stderr)
            continue
        deadline = _compute_deadline(awareness, req)
        jurisdiction_statuses.append(JurisdictionStatus(
            jurisdiction_code=code.upper(),
            deadline_utc=deadline.isoformat() if deadline else None,
            status="pending",
            filed_at=None,
            reference_number=None,
        ))

    incident = BreachIncident(
        incident_id=incident_id,
        awareness_time_utc=awareness_time,
        created_at=now_utc,
        description=description,
        jurisdictions=jurisdiction_statuses,
    )
    incidents[incident_id] = incident
    _save_incidents(incidents)
    print(f"[+] Registered breach incident {incident_id}")
    _print_status(incident)
    return incident


def mark_filed(incident_id: str, jurisdiction_code: str, reference_number: str = "") -> None:
    incidents = _load_incidents()
    incident = incidents.get(incident_id)
    if not incident:
        print(f"[!] Incident {incident_id} not found.", file=sys.stderr)
        return

    now_utc = datetime.now(timezone.utc).isoformat()
    for js in incident.jurisdictions:
        if js.jurisdiction_code == jurisdiction_code.upper():
            js.status = "filed"
            js.filed_at = now_utc
            js.reference_number = reference_number
            print(f"[+] Marked {jurisdiction_code} notification as filed for {incident_id}")
            if reference_number:
                print(f"    Reference: {reference_number}")
            break
    else:
        print(f"[!] Jurisdiction {jurisdiction_code} not tracked for {incident_id}", file=sys.stderr)
        return

    _save_incidents(incidents)


def get_status(incident_id: str) -> None:
    incidents = _load_incidents()
    incident = incidents.get(incident_id)
    if not incident:
        print(f"[!] Incident {incident_id} not found.", file=sys.stderr)
        return
    _print_status(incident)


def _print_status(incident: BreachIncident) -> None:
    now = datetime.now(timezone.utc)
    awareness = _parse_utc(incident.awareness_time_utc)
    elapsed = now - awareness
    elapsed_str = f"{int(elapsed.total_seconds() // 3600)}h {int((elapsed.total_seconds() % 3600) // 60)}m"

    print(f"\n{'='*70}")
    print(f"Incident: {incident.incident_id}")
    print(f"Awareness: {incident.awareness_time_utc} ({elapsed_str} ago)")
    print(f"Status: {incident.status.upper()}")
    if incident.description:
        print(f"Description: {incident.description}")
    print(f"\nNotification Deadlines:")
    print(f"{'Jurisdiction':<20} {'Deadline (UTC)':<25} {'Remaining':<15} {'Status':<12} {'Reference'}")
    print("-" * 90)

    for js in sorted(incident.jurisdictions, key=lambda x: x.deadline_utc or "9999"):
        req = JURISDICTION_REQUIREMENTS.get(js.jurisdiction_code, None)
        name = req.jurisdiction_name if req else js.jurisdiction_code

        if js.deadline_utc:
            deadline = _parse_utc(js.deadline_utc)
            remaining = deadline - now
            if js.status == "filed":
                remaining_str = "FILED"
                flag = "✓"
            elif remaining.total_seconds() < 0:
                hours_over = abs(int(remaining.total_seconds() // 3600))
                remaining_str = f"OVERDUE {hours_over}h"
                flag = "!!!"
            elif remaining.total_seconds() < 3600 * 4:
                remaining_str = f"{int(remaining.total_seconds() // 3600)}h {int((remaining.total_seconds() % 3600) // 60)}m"
                flag = "URGENT"
            else:
                remaining_str = f"{int(remaining.total_seconds() // 3600)}h {int((remaining.total_seconds() % 3600) // 60)}m"
                flag = ""
        else:
            remaining_str = "ASAP"
            flag = "~"

        status_display = js.status.upper()
        ref = js.reference_number or ""
        print(f"{name[:19]:<20} {(js.deadline_utc or 'N/A')[:24]:<25} {remaining_str:<15} {status_display:<12} {ref} {flag}")

    print("="*70)


def run_monitor() -> None:
    """Check all active incidents for approaching or missed deadlines. Run from cron."""
    incidents = _load_incidents()
    now = datetime.now(timezone.utc)
    alerts = []

    for incident in incidents.values():
        if incident.status == "closed":
            continue
        for js in incident.jurisdictions:
            if js.status == "filed":
                continue
            if js.deadline_utc is None:
                continue
            deadline = _parse_utc(js.deadline_utc)
            remaining = deadline - now
            hours_remaining = remaining.total_seconds() / 3600

            req = JURISDICTION_REQUIREMENTS.get(js.jurisdiction_code)
            regulator = req.primary_regulator if req else js.jurisdiction_code

            if hours_remaining < 0:
                alerts.append({
                    "level": "CRITICAL",
                    "incident": incident.incident_id,
                    "jurisdiction": js.jurisdiction_code,
                    "regulator": regulator,
                    "message": f"OVERDUE by {abs(int(hours_remaining))}h — notify {regulator} immediately",
                })
            elif hours_remaining <= 4:
                alerts.append({
                    "level": "URGENT",
                    "incident": incident.incident_id,
                    "jurisdiction": js.jurisdiction_code,
                    "regulator": regulator,
                    "message": f"{hours_remaining:.1f}h remaining — file NOW",
                })
            elif hours_remaining <= 12:
                alerts.append({
                    "level": "WARNING",
                    "incident": incident.incident_id,
                    "jurisdiction": js.jurisdiction_code,
                    "regulator": regulator,
                    "message": f"{hours_remaining:.1f}h remaining to notify {regulator}",
                })

    if alerts:
        _send_alerts(alerts)
        for a in alerts:
            print(f"[{a['level']}] {a['incident']} / {a['jurisdiction']}: {a['message']}")
    else:
        print(f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] Monitor: No pending deadline alerts.")


def _send_alerts(alerts: list[dict]) -> None:
    """Send email alerts. Configure via environment variables."""
    smtp_host = os.environ.get("BREACH_SMTP_HOST")
    smtp_port = int(os.environ.get("BREACH_SMTP_PORT", "587"))
    smtp_user = os.environ.get("BREACH_SMTP_USER")
    smtp_pass = os.environ.get("BREACH_SMTP_PASS")
    alert_to = os.environ.get("BREACH_ALERT_TO")

    if not all([smtp_host, smtp_user, smtp_pass, alert_to]):
        print("[!] SMTP not configured — alerts printed to stdout only.")
        return

    assert smtp_host is not None
    assert smtp_user is not None
    assert smtp_pass is not None
    assert alert_to is not None

    critical = [a for a in alerts if a["level"] == "CRITICAL"]
    subject = (
        f"CRITICAL: Breach notification deadline OVERDUE — {critical[0]['incident']}"
        if critical else
        f"WARNING: Breach notification deadline approaching — {alerts[0]['incident']}"
    )

    body_lines = ["Breach Notification Deadline Alert", "=" * 40, ""]
    for a in alerts:
        body_lines.append(f"[{a['level']}] Incident: {a['incident']}")
        body_lines.append(f"  Jurisdiction: {a['jurisdiction']} — {a['regulator']}")
        body_lines.append(f"  {a['message']}")
        body_lines.append("")

    body_lines.append("Use `breach_notification_tracker.py status --incident-id <ID>` for full details.")
    body_lines.append(f"\nSee Ch 35 and Ch 39 for notification templates.")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = alert_to
    msg["Subject"] = subject
    msg.attach(MIMEText("\n".join(body_lines), "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[+] Alert email sent to {alert_to}")
    except Exception as exc:
        print(f"[!] Failed to send alert email: {exc}", file=sys.stderr)


def print_jurisdictions() -> None:
    print("\nSupported Jurisdiction Codes:")
    print(f"{'Code':<6} {'Name':<35} {'Deadline':<20} {'Primary Regulator'}")
    print("-" * 90)
    for code, req in JURISDICTION_REQUIREMENTS.items():
        if req.deadline_hours:
            deadline_str = f"{int(req.deadline_hours)}h"
        elif req.deadline_business_days:
            deadline_str = f"{req.deadline_business_days} business days"
        else:
            deadline_str = "As soon as feasible"
        print(f"{code:<6} {req.jurisdiction_name[:34]:<35} {deadline_str:<20} {req.primary_regulator}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Breach notification deadline tracker for iGaming operators",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # register
    reg_p = subparsers.add_parser("register", help="Register a new breach incident")
    reg_p.add_argument("--incident-id", required=True)
    reg_p.add_argument("--awareness-time", required=True, help="ISO8601 UTC e.g. 2026-04-03T03:15:00Z")
    reg_p.add_argument("--jurisdictions", required=True, help="Comma-separated codes e.g. EU,UK,MT")
    reg_p.add_argument("--description", default="")

    # status
    st_p = subparsers.add_parser("status", help="Show status of an incident")
    st_p.add_argument("--incident-id", required=True)

    # filed
    fi_p = subparsers.add_parser("filed", help="Mark a jurisdiction notification as filed")
    fi_p.add_argument("--incident-id", required=True)
    fi_p.add_argument("--jurisdiction", required=True)
    fi_p.add_argument("--reference-number", default="")

    # monitor
    subparsers.add_parser("monitor", help="Check all incidents for approaching deadlines")

    # list-jurisdictions
    subparsers.add_parser("list-jurisdictions", help="Show all supported jurisdiction codes")

    args = parser.parse_args()

    if args.command == "register":
        codes = [c.strip() for c in args.jurisdictions.split(",")]
        register_incident(args.incident_id, args.awareness_time, codes, args.description)
    elif args.command == "status":
        get_status(args.incident_id)
    elif args.command == "filed":
        mark_filed(args.incident_id, args.jurisdiction, args.reference_number)
    elif args.command == "monitor":
        run_monitor()
    elif args.command == "list-jurisdictions":
        print_jurisdictions()


if __name__ == "__main__":
    main()
