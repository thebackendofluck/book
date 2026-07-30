# GDPR Supervisory Authority Breach Notification
## Article 33 UK GDPR / GDPR — Initial Notification

**Applicable regulation:** UK GDPR Art. 33 / GDPR Art. 33  
**Submit to:** ICO (UK): ico.org.uk/report | Lead DPA (EU): edpb.europa.eu/about-edpb/members  
**Deadline:** Within 72 hours of awareness  
**Supplemental reports permitted under:** UK GDPR Art. 33(4) / GDPR Art. 33(4)

---

**TO:** [ICO / Lead Supervisory Authority Name]  
**FROM:** [Data Controller Legal Name]  
**Company Registration:** [Number]  
**Date of Notification (UTC):** [YYYY-MM-DDTHH:MM:SSZ]  
**RE:** Personal Data Breach Notification — Article 33 [UK GDPR / GDPR]

---

## 1. Controller and DPO Details

| Field | Detail |
|---|---|
| Controller legal name | [Full registered name] |
| Registered address | [Address] |
| ICO / DPA registration number | [Registration number] |
| DPO name | [Full name] |
| DPO email | [dpo@company.com] |
| DPO phone | [+XX XXX XXXX XXXX] |
| External representative (if applicable) | [Name and contact] |

## 2. Nature of the Breach

| Field | Detail |
|---|---|
| Breach type | [Unauthorised access / Exfiltration / Ransomware / Insider / Physical loss] |
| Discovery method | [SIEM alert / UEBA anomaly / External report / Player complaint] |
| Estimated breach start | [Date/Time UTC or "Under investigation"] |
| Controller became aware | [Date/Time UTC — this is when the 72h clock started] |
| Is breach ongoing? | [Yes / No / Contained] |
| Systems affected | [e.g., Player database, KYC document store, Payment processing logs] |

### Categories of personal data involved

- [ ] Name, email address, username
- [ ] Date of birth
- [ ] Address / phone number
- [ ] Government-issued ID number (passport, national ID, driving licence)
- [ ] Payment card data (note: typically held by PCI-DSS processor, not operator)
- [ ] Bank account details
- [ ] Deposit and withdrawal history
- [ ] Gambling behaviour and session history
- [ ] Self-exclusion or responsible gambling status
- [ ] IP address and device identifiers
- [ ] Other: [specify]

### Scope

| Metric | Value |
|---|---|
| Approximate number of data subjects affected | [Number] or "Under investigation — estimated [range]" |
| Approximate number of records affected | [Number] |
| Jurisdictions of affected data subjects | [e.g., UK: ~45,000; Germany: ~12,000; Netherlands: ~8,000] |

## 3. Likely Consequences

[Describe the likely consequences for affected data subjects. Be specific about risk level:

HIGH RISK examples (require Art. 34 player notification):
- Government ID numbers could enable identity theft or fraudulent account opening
- Payment data or account credentials could enable financial fraud
- Self-exclusion status exposure could cause harm to vulnerable individuals

LOWER RISK examples (notification may not be required):
- Email addresses only, where phishing risk is limited by other controls
- Hashed passwords (bcrypt/Argon2) without evidence of hash cracking capability
- Data encrypted at rest with no evidence key was compromised]

## 4. Measures Taken and Proposed

### Containment (already implemented)

- [e.g., Affected database servers isolated from external egress at [Time] on [Date]]
- [e.g., Compromised service accounts disabled and credentials rotated at [Time]]
- [e.g., Unauthorised access path (CVE-XXXX-XXXXX / misconfigured [component]) patched]
- [e.g., Full forensic investigation commenced — forensics firm [Name] engaged]

### Remediation (underway or planned)

- [e.g., Affected systems being rebuilt from hardened baseline AMIs]
- [e.g., All service account credentials rotated organisation-wide]
- [e.g., MFA enforcement extended to all administrative access]
- [e.g., SIEM/UEBA rules updated with indicators of compromise from this incident]

### Player impact mitigation

- [e.g., Mandatory password reset for all [N] affected accounts]
- [e.g., Player notifications being sent by [method] by [date]]
- [e.g., [X] months complimentary identity monitoring offered to affected players]

## 5. Art. 34 Player Notification Assessment

Based on current information, we assess the risk to data subjects as:

[ ] **High risk** — Player notifications will be sent without undue delay (by [date])  
[ ] **Not yet determined** — Assessment ongoing; will update within [X] hours  
[ ] **Not high risk** — Reasons: [e.g., data was encrypted / scope limited to non-sensitive fields]

## 6. Further Information

This is an initial notification under Art. 33(4). A supplemental notification with complete forensic findings will be provided by [Date — typically 14-30 days].

[Organisation] is co-operating fully with [ICO/DPA] and will provide all requested documentation and access to personnel promptly.

**Signed:**  
[Name]  
[Title]  
[Date]

---

*See also: ICO personal data breach guide — ico.org.uk/for-organisations/report-a-breach/*  
*EDPB Guidelines 9/2022 on breach notification: edpb.europa.eu*
