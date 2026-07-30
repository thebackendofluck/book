# Privacy Rights Guide — AcmeToCasino Platform

## Why Privacy Rights Matter in iGaming

iGaming platforms hold some of the most sensitive personal data that exists: financial transactions, behavioural patterns, health-adjacent data (problem gambling assessments), identity documents, and, in many cases, data about addiction. Privacy law does not simply impose obligations — it codifies the principle that individuals retain meaningful control over information about themselves.

For a multi-jurisdiction operator, this means navigating five distinct privacy regimes simultaneously, each with different legal bases, different rights, and critically different obligations that sometimes conflict with AML law.

---

## Data Subject Rights by Jurisdiction

The table below maps each privacy right to the specific legal provision that creates it. The "WHY" column explains the legal rationale behind each right.

| Right | GDPR | UK GDPR | LGPD | PIPEDA | CCPA/CPRA | Conflict |
|-------|------|---------|------|--------|-----------|---------|
| **Right to Access (SAR)** | Art.15 | Art.15 | Art.18(I/II) | Principle 9 | §1798.100 | None |
| **Right to Rectification** | Art.16 | Art.16 | Art.18(III) | Principle 6 | — | None |
| **Right to Erasure** | Art.17 | Art.17 | Art.18(VI) | — | §1798.105 | **AML retention** |
| **Right to Portability** | Art.20 | Art.20 | Art.18(V) | — | — | None |
| **Right to Restriction** | Art.18 | Art.18 | — | — | — | None |
| **Right to Object** | Art.21 | Art.21 | Art.18(IV) | — | §1798.120 | None |
| **Right not to be profiled** | Art.22 | Art.22 | Art.20 | — | — | **RG profiling** |
| **Right to withdraw consent** | Art.7(3) | Art.7(3) | Art.18(IX) | Principle 3 | — | None |

### WHY Each Right Exists

**Right to Access** — Citizens cannot exercise any other privacy right without first knowing what data an organisation holds about them. Access (Subject Access Request, SAR) is the gateway right. GDPR Art.15 requires a response within 30 days. UKGC-regulated operators should note that the ICO's enforcement record shows SAR failures are the most frequently investigated complaint category in the gambling sector.

**Right to Rectification** — Inaccurate data causes concrete harm: incorrect KYC records can lock players out of their accounts, wrong addresses generate misdirected communications, and incorrect problem gambling flags can trigger unjustified restrictions. Rectification exists because the cost of correction falls on the organisation, not the individual.

**Right to Erasure ("Right to be Forgotten")** — Individuals should not be permanently defined by their past interactions with a platform. GDPR Art.17 reflects the principle that consent-based processing should be reversible. The right is not absolute: it yields to legal obligations (AML retention), vital interests, and public interest tasks.

**Right to Portability** — GDPR Art.20 was designed to enable competition: if a player can export their full history in machine-readable format, they can take it to a competing operator. In iGaming, this is particularly relevant for responsible gaming history — a player who has self-excluded at one operator should be able to share that history with another.

**Right to Restriction** — When a player disputes a transaction, or contests whether data processing is lawful, they should be able to pause processing while the dispute is resolved, without having their account entirely deleted. Restriction is the procedural middle ground.

**Right to Object** — GDPR Art.21 applies specifically to processing based on legitimate interest or for direct marketing. Players have an absolute right to stop marketing at any time. The right to object to legitimate-interest processing can be overridden if the operator can demonstrate compelling legitimate grounds.

**Right not to be profiled** — GDPR Art.22 addresses the power imbalance created by fully automated decision-making. In iGaming, this matters because AI-driven risk scoring can result in account restrictions or bonus exclusions without human review. The right does not prohibit profiling outright — it requires either human oversight, explicit consent, or contractual necessity.

**Right to withdraw consent** — Where consent is the legal basis for processing (e.g., marketing emails, analytics cookies), withdrawal must be as easy as giving consent. The right is prospective — it does not invalidate past processing carried out under valid consent.

---

## The Erasure vs AML Conflict — The Core Legal Tension in iGaming

This is the single most important privacy law complexity facing gambling operators.

### The Competing Obligations

**Player's right (GDPR Art.17):**
> The data subject shall have the right to obtain from the controller the erasure of personal data concerning him or her without undue delay.

**Operator's obligation (AMLD6 Art.40 / UK MLR 2017 Reg.40):**
> Obliged entities shall retain copies of documents and information obtained during customer due diligence measures for five years after the end of the business relationship.

**MGA Directive (Malta):**
KYC records, including identity documents and source-of-funds evidence, must be available for regulatory audit for five years after account closure.

**UKGC LCCP SR Code 3.4.1:**
Responsible gaming records, including self-exclusion history and problem gambling interventions, must be retained to demonstrate compliance.

### Why Simple Deletion is Impossible

If an operator were to fully comply with an erasure request by deleting all records of a player, it would simultaneously:

1. Commit an AML regulatory breach (potential criminal liability under POCA 2002 in the UK, or AMLD6 in the EU)
2. Obstruct a potential law enforcement request
3. Lose evidence needed to defend against disputed transactions
4. Lose the self-exclusion record that prevents a re-registration under a different identity

### The Resolution: Pseudonymisation

GDPR Recital 26 and Art.4(5) explicitly contemplate pseudonymisation as a data protection technique. Pseudonymised data — data that can no longer be attributed to a specific individual without additional information held separately — is outside the scope of the erasure obligation when the re-identification key has been destroyed.

**The pseudonymisation approach for iGaming erasure:**

```
BEFORE erasure:          AFTER erasure (pseudonymised):
─────────────────        ──────────────────────────────
name: John Smith    →    name: [SHA-256 hash, key destroyed]
email: j@ex.com     →    email: [SHA-256 hash, key destroyed]
phone: +44...       →    phone: [NULL]
address: 12 High St →    address: [NULL]
date_of_birth: ...  →    date_of_birth: [NULL]
IP addresses        →    ip_log: [NULL]

RETAINED (no change):
player_id: acm-a4f9b2c1   ← internal reference only, no re-identification possible
transaction_history        ← required by AMLD6
self_exclusion_status      ← required by UKGC LCCP / MGA
kyc_verification_status    ← required by AML directives
aml_alerts                 ← required by FATF/AMLD6
responsible_gaming_flags   ← required by UKGC / MGA
```

**Why this satisfies GDPR:** Once the re-identification key is destroyed, the remaining records no longer constitute personal data under GDPR Art.4(1) because they cannot be traced back to an identified or identifiable natural person.

**Why this satisfies AML law:** The transaction skeleton — amounts, dates, counterparties, risk flags — remains intact. Regulators and law enforcement can still audit financial flows and risk assessments.

### Jurisdictional Variations in the Conflict

| Jurisdiction | Erasure Right | AML Retention | Resolution |
|---|---|---|---|
| GDPR (EU/EEA) | Art.17, 30-day response | AMLD6 Art.40, 5 years | Pseudonymisation; Art.17(3)(b) exception for legal obligation |
| UK GDPR | Art.17, 30-day response | UK MLR 2017 Reg.40, 5 years | Same as GDPR; ICO guidance endorses pseudonymisation |
| LGPD (Brazil) | Art.18(VI) | BACEN Circular 3978, 5 years | Anonymisation (Art.5(XI)); same practical approach |
| PIPEDA (Canada) | Principle 9 (limited right) | FINTRAC, 5 years | Data minimisation; no formal erasure right, but accuracy principle applies |
| CCPA/CPRA | §1798.105 | FinCEN BSA, 5 years | Statutory exemption for legal compliance |

---

## The Responsible Gaming vs Profiling Conflict

### The Competing Obligations

**Player's right (GDPR Art.22):**
> The data subject shall have the right not to be subject to a decision based solely on automated processing, including profiling, which produces legal effects concerning him or her or similarly significantly affects him or her.

**Operator's obligation (UKGC LCCP SR Code 3.4.3):**
> Licensees must use player interaction data to identify customers who may be at risk of or experiencing harms associated with gambling and interact with those customers in a way that minimises harm.

The UKGC's 2023 Consumer Protection requirements explicitly require operators to use automated analysis of player behaviour to identify problem gambling indicators. Failing to do so is a licensing breach.

### Why This Conflict Exists

GDPR Art.22 was designed to prevent algorithmic discrimination — credit refusals, insurance denials, job rejections made by machines without human review. The drafters did not anticipate that automated profiling would be simultaneously a fundamental player protection requirement.

### The Legal Resolution: Legitimate Interest (GDPR Art.6(1)(f))

When processing is necessary for the purposes of the legitimate interests pursued by the controller or a third party, and those interests are not overridden by the interests or fundamental rights of the data subject, the processing is lawful.

**The legitimate interest argument for RG profiling:**

1. **Interest identified:** Preventing gambling-related harm to players and third parties.
2. **Necessity test:** Automated analysis of high-volume behavioural data cannot practically be replaced by manual review. It is necessary.
3. **Balancing test:** The player's right not to be profiled is outweighed by their own interest in being protected from harm, and by the public interest in preventing addiction.

The balancing test is strengthened by the regulatory mandate — a UKGC-licensed operator *must* profile. The regulator has made the legitimacy determination on behalf of the state.

**Practical implementation:**

- Document the legitimate interest assessment in your Record of Processing Activities (ROPA)
- Ensure human review is available for any significant automated decision (account restriction, bonus removal)
- Disclose in the privacy notice that behavioural analysis is conducted for player protection purposes
- Do not use the same profiling data for commercial purposes (upselling) without a separate legal basis — cross-purpose use will fail the balancing test

---

## Self-Exclusion Data — Exempt from Erasure

Self-exclusion records occupy a unique position: they are simultaneously personal data about a player's health-adjacent circumstances and a regulatory requirement.

**Why self-exclusion records survive erasure requests:**

1. **Vital interests (GDPR Art.9(2)(c)):** Processing special-category data (health-adjacent data) is lawful when necessary to protect vital interests.
2. **Regulatory obligation:** GAMSTOP, Spelpaus, and ROFUS require operators to maintain exclusion records to prevent re-registration.
3. **Player's own prior consent:** The self-exclusion request constitutes the player's explicit instructions that they should not be permitted to gamble. Erasing the record would contradict those instructions.

**The operator's response to an erasure request that covers self-exclusion data:**
> We are unable to erase your self-exclusion record because doing so would conflict with our regulatory obligations under [UKGC LCCP / MGA Directive] and would expose you to the risk of gambling harm that you asked us to protect you from. We have pseudonymised all other personal data. Your self-exclusion remains active until [date].

---

## Cross-Border Data Subject Requests

When a player in one jurisdiction makes a request under that jurisdiction's law, the operator must determine which law applies.

| Scenario | Applicable Law | Response Framework |
|---|---|---|
| EU player, MGA-licensed operator | GDPR | 30-day response, Art.15–22 rights |
| UK player, UKGC-licensed operator | UK GDPR | 30-day response, ICO guidance |
| Brazilian player, operator with Brazilian presence | LGPD | 15-day response under ANPD guidance |
| Ontario player, AGCO-licensed operator | PIPEDA + Ontario privacy law | Reasonable timeframe (30 days) |
| California resident, operator accepting CA players | CCPA/CPRA | 45-day response, specific disclosures |

**When the same request triggers multiple laws:** Use the most restrictive standard. If a player is both an EU resident and a CCPA-qualifying California resident (possible for dual nationals), apply GDPR requirements (stricter on most dimensions).

---

## Implementation Notes for Compliance Teams

### Record of Processing Activities (ROPA) — Required by GDPR Art.30

Your ROPA must document, for each processing activity:
- Purpose and legal basis
- Categories of data subjects and personal data
- Recipients (including third-country transfers)
- Retention periods
- Security measures

For iGaming, the minimum ROPA entries are:
1. Player registration and KYC
2. Transaction processing (AML basis)
3. Responsible gaming profiling (legitimate interest basis)
4. Marketing communications (consent basis)
5. Regulatory reporting (legal obligation basis)
6. Fraud detection (legitimate interest basis)

### Privacy Notice Requirements

Under GDPR Art.13, the privacy notice must disclose at collection time:
- Identity and contact details of the controller
- DPO contact details (if DPO appointed — required for large-scale processing)
- Purposes and legal bases for each processing activity
- Legitimate interests relied upon (Art.13(1)(d))
- Recipients and third-country transfers
- Retention periods
- All data subject rights, including the right to lodge a complaint with the supervisory authority

### Data Protection Officer

GDPR Art.37 requires a DPO when core activities involve large-scale, regular, and systematic monitoring of individuals. iGaming operators processing data for hundreds of thousands of players will virtually always meet this threshold. The DPO must be independent, expert, and reachable by data subjects.

---

## See Also

- [`privacy_service.py`](privacy_service.py) — Subject Access Request and rights request handling
- [`erasure_handler.py`](erasure_handler.py) — Pseudonymisation implementation
- [`sar_export.py`](sar_export.py) — GDPR Art.20 portability export
- [`consent_manager.py`](consent_manager.py) — Consent lifecycle management
- [`privacy_impact_assessment.md`](privacy_impact_assessment.md) — DPIA template
