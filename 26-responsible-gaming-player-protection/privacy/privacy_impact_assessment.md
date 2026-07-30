# Data Protection Impact Assessment (DPIA)
## AcmeToCasino Platform — Multi-Jurisdiction iGaming

**Document reference:** DPIA-2026-001
**Version:** 1.0
**Prepared by:** Data Protection Officer
**Date:** 2026-03-30
**Review date:** 2027-03-30
**Legal basis for DPIA:** GDPR Art.35 — processing likely to result in high risk to natural persons

---

## 1. Necessity and Proportionality

### 1.1 Why a DPIA is Required

GDPR Art.35(1) requires a DPIA where processing is likely to result in a high risk to the rights and freedoms of natural persons. The EDPB's guidelines on Art.35 identify nine criteria; processing that meets two or more triggers a mandatory DPIA.

This platform meets the following criteria:

| Criterion | Present? | Basis |
|---|---|---|
| Evaluation or scoring (including profiling) | YES | Responsible gaming risk scoring, addiction detection models |
| Automated decision-making with legal or similarly significant effect | YES | Account restrictions, bonus exclusions, self-exclusion recommendations |
| Systematic monitoring | YES | Real-time behavioural monitoring of all player activity |
| Sensitive or highly personal data | YES | Health-adjacent data (PGSI scores, addiction indicators), financial data |
| Large-scale processing | YES | Potentially millions of players across multiple jurisdictions |
| Matching or combining datasets | YES | Combining session data, financial data, and behavioural signals |
| Vulnerable data subjects | YES | Problem gamblers are a recognised vulnerable group |

All seven criteria are met. A DPIA is not merely advisable — it is mandatory under GDPR Art.35.

### 1.2 Description of Processing

**Controller:** AcmeToCasino Ltd
**DPO:** As appointed; contact dpo@acmetocasino.com
**Processors:** Payment processors, KYC providers, cloud infrastructure providers, national exclusion register operators (GAMSTOP, Spelpaus, ROFUS)

**Processing activities covered by this DPIA:**

1. Player registration and identity verification (KYC)
2. Real-time behavioural monitoring for responsible gaming
3. AI/ML-driven addiction risk scoring
4. Automated deposit limit and session limit enforcement
5. Self-exclusion management and national register synchronisation
6. AML transaction monitoring
7. Marketing profiling and personalisation
8. Regulatory reporting to UKGC, MGA, GGL, AGCO, ANPD

---

## 2. Processing Activities and Legal Bases

| Processing Activity | Personal Data Categories | Legal Basis | Retention |
|---|---|---|---|
| Registration / KYC | Name, DOB, address, ID documents, photo | Contract (Art.6(1)(b)) + Legal obligation (Art.6(1)(c)) | 5 years post-closure |
| RG behavioural monitoring | Session data, wager history, deposit patterns | Legitimate interest (Art.6(1)(f)) — LIA documented in Annex A | 5 years post-closure |
| PGSI/SOGS screening | Health-adjacent responses | Vital interests (Art.9(2)(c)) + Legitimate interest | 5 years post-closure |
| AML monitoring | Transaction amounts, payment methods, geolocation | Legal obligation (Art.6(1)(c)) — AMLD6 / UK MLR 2017 | 5 years post-closure |
| Marketing email/SMS | Name, email, phone, preference data | Consent (Art.6(1)(a)) — withdrawn at any time | Until consent withdrawn |
| Behavioural analytics | Click-stream, game selection, session patterns | Consent (Art.6(1)(a)) | 13 months |
| Third-party sharing | As above | Explicit consent (Art.6(1)(a)) + SCCs for transfers | Per third-party agreement |
| Self-exclusion registration | Player ID hash, exclusion dates | Legal obligation + Vital interests (Art.9(2)(c)) | Duration + 5 years |

---

## 3. Necessity and Proportionality Assessment

### 3.1 Behavioural Monitoring — Is It Necessary?

**Question:** Could the responsible gaming objective be achieved with less data?

**Assessment:**
- Real-time risk scoring requires session data, deposit data, and wager data simultaneously
- Clinical research (PGSI methodology) confirms that no single data point is sufficient — composite scoring is necessary
- Manual review at scale is not feasible: at 100,000+ active sessions, automated monitoring is the only proportionate approach
- Conclusion: Processing is necessary. The data minimisation principle is satisfied by limiting collection to the signals listed in the behavioural risk model.

### 3.2 AML Monitoring — Is 5-Year Retention Proportionate?

**Question:** Why retain data for 5 years rather than a shorter period?

**Assessment:**
- AMLD6 Art.40 prescribes exactly 5 years. This is not a discretionary choice.
- FATF Recommendation 11 (record-keeping) aligns with the 5-year period.
- Shorter retention would be a regulatory breach, not a privacy improvement.
- Conclusion: Retention is proportionate; the period is set by law.

### 3.3 The Erasure vs AML Conflict

**Risk identified:** A player exercising GDPR Art.17 (right to erasure) may believe their data has been fully deleted, while the operator retains pseudonymised transaction records.

**Mitigation:**
- Privacy notice clearly discloses that erasure is implemented via pseudonymisation
- Erasure response to the player explicitly states what was pseudonymised and what was retained
- Erasure certificate provided to player with record reference
- Pseudonymised records are not personal data under GDPR — no ongoing privacy risk

**Residual risk:** LOW

---

## 4. Risks to Data Subjects

| Risk | Likelihood | Severity | Risk Level | Mitigation |
|---|---|---|---|---|
| Player data breach (hackers) | Medium | High | HIGH | Encryption at rest and in transit, PCI DSS, pen testing |
| Inappropriate use of RG profiles for marketing | Medium | High | HIGH | Separate legal bases; RG data access controls |
| Self-exclusion record deletion (re-registration) | Low | Very High | HIGH | Self-exclusion exempt from erasure; registry sync |
| Incorrect risk score → unjustified restriction | Medium | Medium | MEDIUM | Human review queue; player appeal process |
| PGSI data shared outside operator | Low | High | MEDIUM | Access controls; PGSI not included in portability exports by default |
| Consent withdrawal not actioned | Low | Medium | LOW | Automated consent enforcement; regular audit |
| AML data misused for marketing | Very Low | High | LOW | Legal basis firewall; data access controls |
| Third-country transfer without adequate safeguards | Low | High | MEDIUM | SCCs in place; BCRs under review for 2026 |

---

## 5. Measures to Address Risk

### 5.1 Technical Measures

- **Encryption:** AES-256 at rest; TLS 1.3 in transit; HSM for key management
- **Access control:** Role-based access; RG data restricted to responsible gaming team
- **Pseudonymisation:** HMAC-SHA-256 with destroyed salt for Art.17 erasure
- **Audit logging:** All access to player PII logged; immutable audit trail
- **Data minimisation:** Only fields required by the specific processing purpose are collected
- **Portability export:** Automated; PGSI scores excluded by default from Art.20 exports
- **Consent enforcement:** Automated suppression of marketing for players without valid consent

### 5.2 Organisational Measures

- **ROPA maintained** and reviewed annually (GDPR Art.30)
- **DPO appointed** and accessible to data subjects
- **Privacy notice** updated and version-controlled; players notified of material changes
- **Training:** All staff with access to player data complete GDPR/privacy training annually
- **Processor contracts:** Art.28 agreements in place with all processors; reviewed annually
- **Breach response:** Documented procedure; 72-hour supervisory authority notification (GDPR Art.33)
- **Legitimate interest assessments:** LIA documented for all Art.6(1)(f) processing (Annex A)

### 5.3 The Profiling Safeguard

For any automated decision that significantly affects a player (account restriction, bonus exclusion, mandatory self-exclusion recommendation):
- Human review available on request
- Player notified of the automated decision and its basis
- Player may contest the decision via the appeals process

---

## 6. Consultation

### 6.1 Internal Consultation

| Stakeholder | Input |
|---|---|
| Compliance team | AML retention requirements, regulatory obligations |
| Responsible gaming team | Processing necessity for harm prevention |
| Engineering | Technical feasibility of pseudonymisation approach |
| Legal | Legitimate interest assessments; processor agreements |

### 6.2 Data Subject Consultation

Per GDPR Art.35(9), consultation with data subjects (or their representatives) was conducted via:
- Review of player feedback and support tickets relating to privacy
- Analysis of SAR requests and common questions
- Review of consumer advocate feedback on responsible gaming practices

No consultation with regulators was deemed necessary for this DPIA (Art.36 prior consultation threshold not reached — residual risk is not "high" after mitigations).

---

## 7. DPO Opinion

The processing described in this DPIA is lawful, necessary, and proportionate. The principal tension — between the erasure right and AML retention obligations — has been resolved via pseudonymisation in a manner that satisfies both GDPR and AMLD6. The legitimate interest basis for responsible gaming profiling is well-founded and documented.

**Recommendation:** Proceed with processing as described. Review DPIA annually or upon material changes to processing activities, technology, or applicable law.

**DPO sign-off:** ________________________________
**Date:** 2026-03-30

---

## Annex A — Legitimate Interest Assessment (LIA) for Responsible Gaming Profiling

**Processing:** Automated behavioural risk scoring and profiling for problem gambling detection
**Legal basis claimed:** GDPR Art.6(1)(f) — legitimate interest

**Step 1 — Purpose test (is the purpose legitimate?):**
Preventing gambling-related harm is a legitimate interest of the controller (commercial and ethical) and a direct interest of the data subject (physical and mental health). It is also a mandated regulatory function under UKGC LCCP, MGA Player Protection Directive, and equivalent requirements in other licensed jurisdictions.

**Step 2 — Necessity test (is the processing necessary?):**
Yes. Real-time automated analysis of high-volume behavioural data cannot practically be replaced by manual review at scale. The specific signals processed (session length, deposit frequency, bet escalation, loss chasing) are the minimum necessary to compute a reliable risk score.

**Step 3 — Balancing test (do the player's interests override the legitimate interest?):**
The player's interest in not being profiled is real but is outweighed by:
- Their own interest in being protected from gambling harm
- The public interest in reducing problem gambling prevalence
- The regulatory mandate (the state has effectively made the legitimacy determination)
- The safeguard of human review for any significant automated decision

**Conclusion:** Legitimate interest is established. Processing is lawful under Art.6(1)(f).

---

## Annex B — Erasure Decision Tree

```
Player submits erasure request
          │
          ▼
Is there an active balance?
  YES → Require withdrawal first
  NO  → Continue
          │
          ▼
Is there an active AML investigation?
  YES → Inform player; cannot proceed until investigation closed
  NO  → Continue
          │
          ▼
Is there an open dispute?
  YES → Restriction may be more appropriate; offer both options
  NO  → Continue
          │
          ▼
Pseudonymise PII fields (HMAC-SHA-256, key destroyed)
          │
          ▼
Retain: transaction history, KYC status, AML alerts, RG flags
          │
          ▼
Self-exclusion records → NEVER ERASED
          │
          ▼
Issue erasure certificate to player
          │
          ▼
Update ROPA — note pseudonymisation date
```
