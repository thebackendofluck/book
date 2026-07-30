# MGA Security Incident Report Template
## Malta Gaming Authority — Cybersecurity Incident Notification

**Submit to:** compliance.mga.org.mt and dpo@mga.org.mt  
**Copy:** IDPC (Malta) at idpc.gov.mt for GDPR obligations  
**Deadline:** "Without undue delay" for critical incidents — file within 24 hours  
**Formal report:** Within 72 hours  
**Reference:** MGA Directive 5 on IT Security; GDPR Art. 33 (via IDPC)

---

**SECURITY INCIDENT NOTIFICATION**  
**Malta Gaming Authority**

**Date:** [YYYY-MM-DD HH:MM CET]  
**MGA Licence Number(s):** [MGA/B2C/XXXX/20XX]  
**Licensed Entity:** [Full legal name as on licence]  
**Registered Address:** [Malta registered address]  
**MGA Compliance Contact:** [Name, Title, +356 XXXX XXXX, email@company.com]  
**Technical Contact (if different):** [Name, CISO/Head of IT, contact]  
**DPO Contact:** [Name, email, phone]

---

## Section 1 — Incident Summary

| Field | Detail |
|---|---|
| Incident reference | [Your internal reference, e.g., INC-2026-042] |
| Incident type | [Data breach / Ransomware / DDoS / System compromise / Insider threat / Fraud attempt] |
| Date/time of occurrence (estimated) | [Date/Time CET] |
| Date/time of detection | [Date/Time CET] |
| Detection method | [Monitoring alert / Player complaint / Third-party report / Regulator notification] |
| Current status | [Contained / Ongoing / Under investigation / Remediated] |
| Platforms/licences affected | [List affected MGA-licensed products/platforms] |
| Has the incident been resolved? | [Yes / No — if yes, resolution time] |

### Brief Narrative

[3-5 sentences describing what happened, how it was detected, and the current state.
Write this as if briefing an MGA compliance officer who needs to understand the incident
in 60 seconds.]

---

## Section 2 — Technical Detail

### Attack Vector

[Describe how the attacker or incident originated. Be specific:
- External intrusion via [phishing / unpatched vulnerability CVE-XXXX / credential stuffing / etc.]
- Insider action by [role — do not name individuals in this filing]
- Third-party/supply chain compromise via [vendor name if relevant]
- Configuration error / accidental exposure]

### Indicators of Compromise (IoCs)

[List specific technical indicators. These help the MGA assess whether the same threat
affects other licensees — they may share with other operators on an anonymised basis.]

| Type | Value | Description |
|---|---|---|
| IP Address | [e.g., 203.0.113.x (documentation range)] | Attacker C2 server |
| Domain | [e.g., fake-provider-update.com] | Phishing domain |
| File hash (SHA-256) | [hash] | Malware sample |
| User agent | [string] | Attacker's automated tool |

### Infrastructure Affected

| System | Type | Function | MGA Relevance |
|---|---|---|---|
| [db-prod-01] | [PostgreSQL server] | [Player database] | [Contains KYC data, transaction history] |
| [app-payments-02] | [Payment service] | [Withdrawal processing] | [PCI-DSS scope] |

---

## Section 3 — Regulatory Impact Assessment

| Question | Answer | Detail |
|---|---|---|
| Player funds at risk or affected? | [Yes/No] | [Specify amount and current status] |
| Withdrawals or deposits affected? | [Yes/No] | [Specify scope and duration] |
| Player funds confirmed safe? | [Yes/No/Under investigation] | [Reconciliation status] |
| Game integrity affected (RNG, game outcomes)? | [Yes/No] | |
| Player personal data involved? | [Yes/No] | [Categories and approximate count] |
| KYC/identity documents exposed? | [Yes/No] | [Specify — passport, national ID, etc.] |
| AML monitoring systems affected? | [Yes/No] | [Detail any gaps in monitoring coverage] |
| Responsible gambling systems affected? | [Yes/No] | [Self-exclusion, deposit limits, etc.] |
| Regulatory reporting feeds affected? | [Yes/No] | [Specify duration of any gaps] |
| Suspected involvement of organised crime? | [Yes/No/Unknown] | |

---

## Section 4 — Response Timeline

| Time (CET) | Action | By |
|---|---|---|
| [Detection time] | Incident detected via [method] | [Role] |
| [+X min] | Escalated to CISO/CTO | [Role] |
| [+X min] | War room assembled | IC: [Name/Role] |
| [+X min] | Network isolation of affected segment | [Network team] |
| [+X min] | Forensic evidence collection commenced | [Forensics firm] |
| [+X min] | DPO notified | [DPO name] |
| [+X min] | External counsel notified | [Firm] |
| [+X h] | Containment confirmed | [Role] |
| [Time] | This MGA notification filed | [Compliance contact] |

---

## Section 5 — IDPC / GDPR Notification Status

| Item | Status |
|---|---|
| Does the incident involve personal data of data subjects? | [Yes/No] |
| GDPR Art. 33 notification required? | [Yes/No] |
| IDPC notification filed? | [Yes / Planned — [date]] |
| IDPC reference number | [If assigned] |
| Art. 34 player notification required? | [Yes/No/Under assessment] |
| Player notification planned by | [Date] |

---

## Section 6 — Player Notification Plan

[If player notification is required under GDPR Art. 34:]

- **Notification method:** [Email / In-app message / SMS / All three]
- **Scope:** Approximately [N] affected players
- **Planned send date:** [Date]
- **Content summary:** [1-2 sentences on what players will be told]
- **Support provision:** [Dedicated email/phone for player queries; identity monitoring offered]

---

## Section 7 — Immediate Remediation Actions

[List actions already completed:]
1. [Action] — completed at [time]
2. [Action] — completed at [time]

[List actions planned:]
1. [Action] — planned by [date]
2. [Action] — planned by [date]

---

## Section 8 — Supplemental Report Commitment

A comprehensive forensic report and post-incident analysis will be submitted to the MGA by
[Date — typically 14-21 days from containment].

The report will include: confirmed scope and root cause, full attacker timeline,
remediation steps completed, and preventive controls implemented.

---

**Declaration**

I declare that the information provided in this notification is accurate and complete to the
best of my knowledge at the time of filing. I understand that this is an initial notification
and that supplemental information will be provided as the investigation progresses.

**Signed:** [Name, Title]  
**Date:** [Date]

---

*For urgent incidents outside business hours, contact the MGA duty officer via:
+356 2546 5500*
