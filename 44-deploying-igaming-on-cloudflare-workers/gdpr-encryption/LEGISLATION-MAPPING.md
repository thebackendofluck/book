# Legislation Mapping: Data Processing Activities

AcmeToCasino Platform — Cloudflare Workers Deployment

This document maps every data processing activity performed by the platform
to the legal basis under each applicable jurisdiction. It is a living document
that must be updated whenever new data processing activities are introduced.

**Jurisdictions covered:**
- EU GDPR (Regulation 2016/679)
- UK GDPR (UK Data Protection Act 2018 + UK GDPR retained law)
- LGPD — Lei Geral de Proteção de Dados (Brazil, Law 13,709/2018)
- ePrivacy Directive (2002/58/EC, as amended)
- AML/CFT: 4th/5th AMLD (EU), FATF Recommendations, POCA 2002 (UK)

---

## 1. Legal Basis for Each Processing Activity

| Processing Activity | EU GDPR Legal Basis | UK GDPR Legal Basis | LGPD Legal Basis | Notes |
|--------------------|--------------------|--------------------|------------------|-------|
| Account registration | Art.6(1)(b) — contract | Art.6(1)(b) | Art.7(V) — contract | Necessary to provide the service |
| Login / authentication | Art.6(1)(b) — contract | Art.6(1)(b) | Art.7(V) — contract | — |
| Balance management | Art.6(1)(b) — contract | Art.6(1)(b) | Art.7(V) — contract | Financial transaction processing |
| KYC identity verification | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | 4AMLD Art.13, MLD5 Art.14 |
| AML transaction monitoring | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | FATF Rec.10, 4AMLD Art.7 |
| Responsible gambling limits | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | UKGC SR Code 3.4, MGA Directive |
| Self-exclusion enforcement | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | UKGC SR Code 3.5, MGA Directive |
| Marketing communications | Art.6(1)(a) — consent | Art.6(1)(a) | Art.7(I) — consent | ePrivacy Art.13 (PECR in UK) |
| Fraud detection / risk scoring | Art.6(1)(f) — legitimate interests | Art.6(1)(f) | Art.7(IX) — legitimate interest | LIA required; player interests assessed |
| Game analytics / personalisation | Art.6(1)(a) — consent | Art.6(1)(a) | Art.7(I) — consent | Cookie consent required (ePrivacy) |
| Session logging | Art.6(1)(b) + Art.6(1)(f) | Art.6(1)(b) | Art.7(V) + Art.7(IX) | Contract performance + security |
| Jurisdiction geo-blocking | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | Gambling licence conditions |
| Audit log retention | Art.6(1)(c) — legal obligation | Art.6(1)(c) | Art.7(II) — legal obligation | See retention schedule below |

---

## 2. Technical Measures (GDPR Art.32)

| Measure | GDPR Article | UK GDPR | LGPD | Implementation |
|---------|-------------|---------|------|----------------|
| Encryption at rest | Art.32(1)(a) | Art.32(1)(a) | Art.46(1) | AES-256-GCM via FieldCipher (field-cipher.ts) |
| Encryption in transit | Art.32(1)(a) | Art.32(1)(a) | Art.46(1) | TLS 1.3 — Cloudflare edge certificate |
| Pseudonymisation | Art.4(5) + Art.25(1) | Art.4(5) | Art.13(IV) | HMAC-SHA-256 via Pseudonymiser (pseudonymiser.ts) |
| Data minimisation | Art.5(1)(c) | Art.5(1)(c) | Art.6(III) | Only required PII columns stored; KYC docs in R2 |
| Purpose limitation | Art.5(1)(b) | Art.5(1)(b) | Art.6(I) | Separate tables per domain; no cross-purpose joins |
| Integrity / availability | Art.32(1)(b) | Art.32(1)(b) | Art.46(1) | D1 automatic backups; Cloudflare 99.99% SLA |
| Access controls | Art.32(1)(b) | Art.32(1)(b) | Art.46(1) | JWT authentication; Workers Secrets; R2 private |
| Regular testing | Art.32(1)(d) | Art.32(1)(d) | Art.46(1) | test-encryption.ts; CI/CD pipeline |

---

## 3. Data Subject Rights

| Right | GDPR Article | UK GDPR | LGPD | Platform Implementation |
|-------|-------------|---------|------|------------------------|
| Right to access | Art.15 | Art.15 | Art.18(I-II) | `GET /api/compliance/data-export` |
| Right to rectification | Art.16 | Art.16 | Art.18(III) | `PUT /api/profile` — EncryptedModel.update() |
| Right to erasure | Art.17(1) | Art.17(1) | Art.18(VI) | Pseudonymiser.erasePlayer() or CryptoShredder.shredPlayer() |
| Right to restriction | Art.18 | Art.18 | Art.18(IV) | Account suspension flag in D1 |
| Right to portability | Art.20 | Art.20 | Art.18(V) | `GET /api/compliance/data-export` (JSON/CSV) |
| Right to object | Art.21 | Art.21 | Art.18(II) | Opt-out of marketing; legitimate interests override |
| Right not to be profiled | Art.22 | Art.22 | Art.20 | No fully automated decisions that produce legal effects |

---

## 4. GDPR vs AML Conflict Resolution

This is the central compliance tension for any iGaming platform. The player's
right to erasure (Art.17) conflicts with the AML obligation to retain records.

**Resolution: GDPR Art.17(3)(b) governs.**

> "The right to erasure does not apply to the extent that processing is
> necessary [...] for compliance with a legal obligation which requires
> processing by Union or Member State law to which the controller is subject."
> — GDPR Art.17(3)(b)

Equivalent provisions:
- UK GDPR Art.17(3)(b): identical
- LGPD Art.16(V): "when necessary to comply with legal or regulatory obligations"

| Data Category | GDPR Right to Erasure | AML Retention Obligation | Resolution |
|--------------|----------------------|--------------------------|------------|
| Name, email, address, phone | Art.17(1) applies | Not required after transaction | **Pseudonymise / shred** |
| Transaction records (amount, currency, type, date) | Art.17(3)(b) overrides | 4AMLD Art.40: 5 years minimum | **Retain intact** |
| KYC documents | Art.17(3)(b) overrides | 4AMLD Art.40: 5 years post-relationship | **Retain in R2** |
| AML risk scores, PEP/sanctions flags | Art.17(3)(b) overrides | FATF Rec.19: indefinite for high-risk | **Retain intact** |
| Self-exclusion flag + duration | Art.17(3)(b) overrides | UKGC SR Code 3.5.3 | **Retain intact — player safety** |
| Session logs | Art.5(1)(e) storage limitation | 4AMLD Art.40 (if transaction-linked) | **12-month retention, then delete** |
| Marketing preferences | Art.17(1) applies | None | **Delete immediately on request** |
| Password hash | Art.17(1) applies after erasure | None | **Delete on account erasure** |

**Practical implementation:**

When a player submits a GDPR Art.17 erasure request:

1. Pseudonymise PII columns: `Pseudonymiser.erasePlayer()` or `CryptoShredder.shredPlayer()`
2. Retain transaction rows: financial columns (amount, currency, type) are plaintext
3. Retain AML fields: never touch `aml_flags`, `kyc_status`, `pep_status`
4. Retain self-exclusion: player safety overrides erasure in all jurisdictions
5. Write audit record: `compliance_events` row documents the erasure action
6. Respond to player: confirm pseudonymisation within 30 days (GDPR Art.12(3))

**AML retention periods:**

| Regulation | Retention Period | Trigger |
|------------|-----------------|---------|
| 4AMLD Art.40 (EU) | 5 years (extendable to 10 by member state) | End of business relationship |
| 5MLD Art.40 (EU) | 5 years | End of business relationship |
| POCA 2002 / MLR 2017 (UK) | 5 years | End of business relationship |
| FinCEN / BSA (US) | 5 years | Transaction date |
| COAF (Brazil) | 5 years | End of business relationship |

---

## 5. Data Residency and Cross-Border Transfers

| Player Jurisdiction | Processing Location | Legal Mechanism | D1 Location Hint |
|--------------------|--------------------|-----------------|--------------------|
| EU (EEA countries) | EU — Cloudflare WEUR/EEUR | GDPR Art.6 — no transfer restriction (intra-EEA) | `weur` or `eeur` |
| UK | EU/UK — Cloudflare WEUR | UK GDPR Art.45 — UK adequacy decision for EEA | `weur` |
| Brazil | US-East — Cloudflare ENAM | LGPD Art.33(I) — adequate protection or SCCs | `enam` (LATAM not yet available) |
| New Jersey (US) | US — Cloudflare ENAM | N.J.A.C. 13:69O-1 | `enam` |
| Other | EU default | GDPR Art.46(2)(c) — SCCs with non-adequate countries | `weur` |

**Cloudflare as Data Processor (GDPR Art.28):**

Cloudflare processes personal data on behalf of the operator (data controller).
This requires a Data Processing Agreement (DPA). Cloudflare's DPA is available
at: https://www.cloudflare.com/gdpr/examinedgdpr/

Required actions for operators:
1. Sign Cloudflare's DPA before processing EU personal data
2. Review Cloudflare's Sub-Processor list (cloudflare.com/gdpr/subprocessors)
3. Implement EU Standard Contractual Clauses (SCCs) via Cloudflare's self-serve process
4. Enable D1 location hints for GDPR data residency (see deploy.sh)
5. Configure Logpush to exclude PII fields from exported logs

**Workers `jurisdiction` field:**

Adding `jurisdiction = "eu"` to `wrangler.toml` restricts the Worker's
execution to EU Cloudflare datacenters. This is available on the Workers Paid
plan and provides the strongest available processing locality guarantee.

```toml
# wrangler.toml — EU-only processing
[env.production]
jurisdiction = "eu"
```

---

## 6. Data Retention Schedule

| Data Type | Retention Period | Legal Basis | Deletion Method |
|-----------|-----------------|-------------|-----------------|
| Active account PII | Lifetime of account | Art.6(1)(b) contract | Erasure on account closure |
| Transaction records | 5 years post-relationship | Art.6(1)(c) / 4AMLD Art.40 | D1 row remains; PII pseudonymised |
| KYC documents | 5 years post-relationship | Art.6(1)(c) / 4AMLD Art.13 | R2 object retained; access revoked |
| Session logs | 12 months | Art.6(1)(f) security | D1 row deletion via cron |
| Security events | 24 months | Art.6(1)(f) security | D1 row deletion via cron |
| AML flags | 5 years minimum (10 for high-risk) | Art.6(1)(c) / FATF Rec.19 | Never deleted |
| Self-exclusion records | 5 years post-exclusion | Art.6(1)(c) / UKGC SR 3.5 | Never deleted |
| Marketing consent records | 3 years | Art.6(1)(a) + Art.7(1) | Delete after 3 years of inactivity |
| Audit/compliance events | 7 years | Art.6(1)(c) / AML | Never deleted during retention window |

---

## 7. Data Protection Officer (DPO) Obligations

| Jurisdiction | Obligation | Trigger | Article |
|-------------|------------|---------|---------|
| EU GDPR | DPO appointment required | Public authority OR large-scale systematic monitoring OR large-scale special category data | Art.37-39 |
| UK GDPR | DPO appointment required | Same triggers as EU GDPR | Art.37-39 (retained UK law) |
| LGPD (Brazil) | Encarregado appointment required | All controllers processing Brazilian personal data | Art.41 |

iGaming operators processing player data at scale almost certainly trigger the
"large-scale systematic monitoring" threshold under Art.37(1)(b). A DPO or
Encarregado should be appointed and their contact details published in the
Privacy Notice.

---

## 8. Breach Notification

| Jurisdiction | Notification to DPA | Notification to Data Subjects | Article |
|-------------|---------------------|------------------------------|---------|
| EU GDPR | 72 hours of becoming aware | Without undue delay if high risk to rights and freedoms | Art.33, Art.34 |
| UK GDPR | 72 hours | Without undue delay if high risk | Art.33-34 (ICO) |
| LGPD (Brazil) | Reasonable timeframe (ANPD guidance: 2 working days for high-risk) | Required for high-risk breaches | Art.48 |
| ePrivacy (EU) | Without undue delay (ISPs/telecoms) | Without undue delay if significant harm | Art.4 |

**Cloudflare breach notification:**
Under the Cloudflare DPA (Art.28 processor agreement), Cloudflare must notify
operators of any security incident affecting their data "without undue delay".
Operators are then responsible for assessing whether the 72-hour DPA clock has
started and notifying the relevant supervisory authority.

---

## 9. Children's Data

| Jurisdiction | Age Threshold | Parental Consent Required | Article |
|-------------|--------------|--------------------------|---------|
| EU GDPR | 16 years (member states may lower to 13) | Yes, for children under threshold | Art.8 |
| UK GDPR | 13 years (ICO Children's Code) | Yes, for children under 13 | Art.8 (UK) |
| LGPD (Brazil) | 12 years | Yes | Art.14 |

**iGaming note:** All gambling operators must implement age verification controls
to prevent access by under-18s (UKGC Licence Condition 17.1.1; MGA Player
Protection Directive Art.6). This is a stricter control than GDPR Art.8 and
subsumes it for gambling purposes.

---

## 10. Supervisory Authority Contacts

| Jurisdiction | Authority | Contact |
|-------------|-----------|---------|
| EU (lead authority) | ICO / relevant member state DPA | edpb.europa.eu |
| UK | Information Commissioner's Office (ICO) | ico.org.uk |
| Malta (MGA operators) | Information and Data Protection Commissioner (IDPC) | idpc.org.mt |
| Gibraltar | Gibraltar Regulatory Authority (GRA) | gra.gi |
| Brazil | Autoridade Nacional de Proteção de Dados (ANPD) | gov.br/anpd |
| New Jersey | NJ Division of Gaming Enforcement (DGE) | nj.gov/oag/ge |

---

*Last reviewed: 2026-03-30. Review annually or upon material change to data processing activities.*
