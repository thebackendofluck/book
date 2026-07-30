# Gambling Compliance Research: Multi-Jurisdiction Website Requirements

Research date: 2026-03-20

---

## 1. Site Analysis: Staging Environment (staging.acmetocasino.com)

The staging site at staging.acmetocasino.com was unreachable due to an expired SSL certificate. The production site acmetocasino.com returned HTTP 405. Analysis below is reconstructed from industry-standard compliance elements observed across regulated UK/MGA casino sites and regulatory documentation.

### Typical Regulated Casino Footer Structure

A compliant online casino footer for dual UK/MGA licensing typically includes:

- **License information**: UKGC license number (e.g., "Licensed and regulated by the Gambling Commission, licence number XXXXX") and MGA license seal with license number (e.g., "Licensed by the Malta Gaming Authority MGA/B2C/XXX/XXXX")
- **18+ badge**: Prominent age restriction logo displayed on every page
- **Responsible gaming logos**: BeGambleAware, GamCare, GamStop (UK); Responsible Gaming Foundation (Malta)
- **Payment method badges**: Visa, Mastercard, PayPal, Paysafecard, bank transfer icons
- **Security badges**: SSL padlock, eCOGRA or iTechLabs certification seal
- **Legal text**: Registered company name, address, and jurisdiction
- **Links**: Terms & Conditions, Privacy Policy, Responsible Gaming, Cookie Policy, Complaints Procedure
- **Cookie consent banner**: GDPR-compliant consent mechanism on first visit
- **Age verification gate**: 18+ confirmation modal before site access (some operators use this, others rely on registration-time verification)

---

## 2. UK Gambling Commission (UKGC) Licensing

### License Types (Remote)

| License Type | Description |
|---|---|
| Remote Casino Operating Licence | Slots, table games, live dealer |
| Remote Betting Operating Licence | Fixed-odds betting, exchange betting |
| Remote Bingo Operating Licence | Online bingo |
| Remote Betting Intermediary | Betting exchanges |
| Remote General Betting (Limited) | Pool betting |
| Ancillary Remote Licence | Software supply |
| Personal Management Licence (PML) | Required for key personnel |

### Application and Annual Fees (Based on GGY)

| GGY Band | Application Fee | Annual Fee |
|---|---|---|
| Under GBP 550,000 | GBP 4,224 | GBP 4,199 |
| GBP 550,000 - 1.5M | GBP 5,523 | GBP 5,468 |
| GBP 1.5M - 3M | ~GBP 8,000 | ~GBP 8,000 |
| GBP 3M+ (scaling) | Up to GBP 91,686 | Up to GBP 793,729 |

Additional costs:
- Personal Management Licence: GBP 185-370 per person
- Software and RNG certification: GBP 15,000-50,000
- Ongoing compliance/reporting: GBP 50,000-100,000/year
- Mandatory gambling levy (introduced April 2025)

### Application Documents Required

1. Certificate of incorporation and memorandum of association
2. Detailed business plan with operational model and revenue projections
3. Bank statements (last 6 months)
4. Proof of funding and financial resources
5. Ownership structure identifying all beneficial owners
6. AML/CTF policy and procedures
7. Responsible gambling policy and customer interaction framework
8. Data protection policy (UK GDPR compliant)
9. Complaints handling procedure
10. Technical standards compliance evidence (RTS)
11. ISO 27001 security audit report
12. Penetration test report (annual requirement)
13. RNG certification from approved test house

### Personal Management Licence (PML) Requirements (2025 Update)

As of 2025, expanded PML requirements mandate that the following roles hold personal licenses:
- CEOs and Managing Directors
- Board Chairs
- Compliance Officers / MLRO
- Finance Directors
- Marketing Directors
- IT Directors / CTO
- Any person with significant management responsibility

### LCCP Requirements (Licence Conditions and Codes of Practice)

The LCCP is divided into Licence Conditions (legally binding) and Social Responsibility Code Provisions (also binding) and Ordinary Code Provisions (expected good practice).

#### Key LCCP Requirements for Remote Operators:

**Customer Interaction (Social Responsibility):**
- Must identify customers at risk of harm
- Must interact with customers showing signs of problem gambling
- Must take action proportionate to the level of risk
- Customer interaction framework must be documented and auditable
- Financial vulnerability checks required for customers with net deposits of GBP 150+ per month (from 28 Feb 2025)

**Deposit Limits (from 31 Oct 2025):**
- Operators must ensure customers set a financial limit before their first deposit
- Customers must actively set and maintain deposit limits

**Bonus and Wagering Requirements (from 19 Jan 2026):**
- Maximum 10x wagering requirement on bonuses
- Cross-selling bonuses combining multiple gambling products banned

**Customer Fund Protection:**
- Must disclose protection level: "not protected - no segregation," "not protected - segregation," "medium protection," or "high protection"

**Direct Marketing (SR Code 5.1.12):**
- Customers must have granular control over marketing preferences

**Fair Terms (from 6 April 2026):**
- Updated to reference Digital Markets, Competition and Consumers Act 2024 (replacing Consumer Protection from Unfair Trading Regulations 2008)

### UKGC Remote Technical Standards (RTS)

14 standards covering:

| RTS | Subject |
|---|---|
| RTS 1 | Customer account information |
| RTS 2 | Financial limits and transaction history |
| RTS 3 | Rules, game descriptions |
| RTS 4 | Return to player (RTP) information |
| RTS 5 | Random number generation |
| RTS 6 | Game and event logs |
| RTS 7 | Underage protection |
| RTS 8 | Fair and transparent terms |
| RTS 9 | Player protection during server failure |
| RTS 10 | Collusion/cheating prevention |
| RTS 11 | Auto-play functionality |
| RTS 12 | Financial limits tools |
| RTS 13 | Time requirements and reality checks |
| RTS 14 | Responsible product design |

Security requirements based on ISO/IEC 27001 with annual audit and penetration testing mandatory.

### AML Requirements

Based on:
- Proceeds of Crime Act 2002 (POCA)
- Terrorism Act 2000
- Money Laundering, Terrorist Financing and Transfer of Funds Regulations 2017
- UKGC AML guidance

**Key obligations:**
- Identity verification BEFORE first deposit (72-hour grace period eliminated in 2025)
- Customer Due Diligence (CDD) at onboarding
- Enhanced Due Diligence (EDD) for: PEPs, high-value customers, FATF grey-list countries, suspicious activity
- Source of Funds (SOF) checks for high depositors
- Source of Wealth (SOW) verification for VIP/high-stakes players
- Ongoing transaction monitoring
- Suspicious Activity Reports (SARs) to NCA
- MLRO (Money Laundering Reporting Officer) appointment mandatory
- Risk assessment updated annually minimum
- Staff AML training records maintained
- Risk ratings reviewed periodically (mandatory from June 2025)

### MANDATORY Website Elements (UK)

| Element | Requirement | Legal Basis |
|---|---|---|
| UKGC license number | Visible on every page | LCCP |
| 18+ age restriction symbol | Prominent on every page | LCCP |
| GamStop link + integration | MANDATORY since March 2020 | LCCP SR Code 3.5.6 |
| BeGambleAware link | MANDATORY | LCCP SR Code 3.4 |
| GamCare link | Recommended (industry standard) | Best practice |
| Deposit limit tools | Prominently accessible | RTS 12, LCCP |
| Session timer / reality checks | Must be available | RTS 13 |
| Self-exclusion option (site-level) | Minimum 6 months | LCCP SR Code 3.5 |
| Time-out / cool-off option | 24hrs to 6 weeks | LCCP SR Code 3.5 |
| Terms & Conditions link | Accessible from every page | LCCP LC 7.1 |
| Privacy Policy | Required (UK GDPR) | Data Protection Act 2018 |
| Complaints procedure link | Must be accessible | LCCP LC 11.1 |
| Cookie consent banner | Required (PECR/GDPR) | PECR Regulation 6 |
| Responsible Gambling page | Comprehensive RG information | LCCP SR Code 3.4 |
| Fund protection disclosure | Must state protection level | LCCP LC 4.2 |

---

## 3. Malta Gaming Authority (MGA) Licensing

### License Types

**B2C (Gaming Service) Licences:**

| Type | Description | Min Annual Fee | Max Annual Fee |
|---|---|---|---|
| Type 1 | Casino-type games (slots, table games, live dealer), lotteries | EUR 15,000 | EUR 375,000 |
| Type 2 | Fixed-odds betting | EUR 25,000 | EUR 600,000 |
| Type 3 | Peer-to-peer games (poker, betting exchange) | EUR 25,000 | EUR 500,000 |
| Type 4 | Controlled skill games | EUR 5,000 | EUR 500,000 |

**B2B (Critical Gaming Supply) Licences:**

| Type | Description | Annual Fee |
|---|---|---|
| Game provider | Software supply to B2C operators | EUR 25,000-35,000 |
| Back office | Platform/back-office services | EUR 3,000-5,000 |

### Costs Summary

- **Application fee**: EUR 5,000 (one-time, non-refundable)
- **Fixed annual licence fee (B2C Types 1-3)**: EUR 25,000
- **Fixed annual licence fee (B2C Type 4)**: EUR 10,000
- **Gaming tax**: 5% on GGR from players based in Malta only; revenue from outside Malta is exempt
- **Compliance surcharge**: Variable based on revenue bands

### Application Process

1. **Pre-application meeting** with MGA officer
2. **Establish Maltese legal entity** with registered office
3. **Submit application** electronically via Licensee Portal
4. **Due diligence**: Background checks on all shareholders, directors, beneficial owners, key personnel
5. **Personal Declaration Forms** required for all directors/shareholders with 5%+ holding
6. **Technical review**: Business plan, financial forecasts, operational policies
7. **System audit**: Staging environment tested by approved auditor
8. **Licence issuance**

### Timeline

4-6 months (up to 16 weeks from complete file submission)

### Required Documents

1. Certificate of incorporation (Maltese entity)
2. Memorandum and articles of association
3. Business plan with 3-year financial projections
4. AML/KYC policy and procedures
5. Responsible gaming policy (compliant with Player Protection Directive)
6. Technical infrastructure documentation
7. Data protection policy (GDPR compliant)
8. Player funds segregation arrangements
9. Shareholder register and beneficial ownership structure
10. Personal Declaration Forms for all key persons
11. Criminal record certificates for directors
12. Proof of source of funds for the company
13. System audit report from MGA-approved auditor
14. RNG certification

### Player Protection Directive (Directive 2 of 2018, updated Jan 2024)

Core requirements:

**Mandatory Player Protection Tools:**
- Self-exclusion: Flexible timeframes from 24 hours to 365 days
- Deposit limits (daily, weekly, monthly)
- Loss limits OR wagering limits
- Session/time limits
- Reality checks during play sessions
- Cool-off periods

**Markers of Harm (5 mandatory monitoring areas):**
1. Amount and frequency of deposits
2. Amount and frequency of wagers
3. Time spent gambling
4. Patterns indicating chasing losses
5. Behavioral changes in gambling activity

**Disclosure Requirements:**
- Underage gaming prohibition signs (prominently displayed)
- Responsible gaming messages on site
- Links to responsible gaming information
- Links to gambling help organizations
- Pre-first-deposit message about responsible gaming tools and limits

**Record-Keeping:**
- Evidence of policy/procedure adherence
- Records of investigations and decisions
- Responsible gaming player interaction logs
- All records retained for minimum period

### MANDATORY Website Elements (Malta)

| Element | Requirement | Legal Basis |
|---|---|---|
| MGA license seal/logo | On every page; must link to MGA verification | Gaming Act, License Conditions |
| 18+ age restriction | Prominently displayed | Player Protection Directive |
| Responsible Gaming Foundation (RGF) link | Malta's primary RG organization | Player Protection Directive |
| RGF Supportline 1777 | Display helpline number | Best practice |
| Self-exclusion tool | Accessible from player account | Player Protection Directive |
| Deposit limit tool | Must be offered | Player Protection Directive |
| Session limit / reality check | Must be offered | Player Protection Directive |
| Cool-off period tool | Must be available | Player Protection Directive |
| Responsible Gaming page | Comprehensive information | Player Protection Directive |
| Terms & Conditions | Accessible | License Conditions |
| Privacy Policy | Required (GDPR) | GDPR |
| BetBlocker / Gamban links | Blocking software references | RGF partnership |
| Pre-deposit RG information | Before first deposit | Player Protection Directive |

**Key difference from UK:** Malta does NOT use GamStop. Self-exclusion is operator-level, with MGA working toward a centralized cross-operator self-exclusion system. The Responsible Gaming Foundation (RGF) at rgf.org.mt is Malta's primary support organization.

---

## 4. Sweden (Spelinspektionen) Licensing

### License Overview

- **Regulator**: Spelinspektionen (Swedish Gambling Authority)
- **License validity**: 5 years maximum, renewable
- **License fee**: EUR 23,760 (SEK 264,000) per year (as of January 2025)
- **Gaming tax**: 18% on GGR

### Key Requirements

1. Swedish legal entity or EU/EEA branch
2. Mandatory integration with Spelpaus self-exclusion system
3. Swedish-registered auditor (annual audit)
4. AML/KYC procedures with appointed compliance officer
5. Responsible gaming policy compliant with Swedish regulations

### Bonus Restrictions (Strict)

- **One welcome bonus only per player, EVER** (across the lifetime of the customer relationship)
- No reload bonuses, cashback, or loyalty programs that function as bonuses
- Strict advertising restrictions monitored by Spelinspektionen

### Upcoming Changes (2026)

- **Credit ban**: Operators prohibited from accepting credit-funded payments (credit cards banned for gambling)
- **Spelpaus API upgrade**: Operators must use secure digital tools (Actor IDs, API keys) for exclusion verification (expected August 2026)

### MANDATORY Website Elements (Sweden)

| Element | Requirement | Legal Basis |
|---|---|---|
| Spelinspektionen license display | On every page | Gambling Act (2018:1138) |
| 18+ age restriction | Prominently displayed | Gambling Act |
| Spelpaus link + integration | MANDATORY (national registry) | Gambling Act Section 14 |
| Spelpaus verification on login | Every login must check registry | Gambling Act |
| Deposit limits (day/week/month) | MANDATORY for all players | Gambling Ordinance |
| 3-second deposit limit confirmation | Player must confirm limits after 3s pause | Gambling Ordinance |
| Session time display | Visible clock/timer | Spelinspektionen guidance |
| Mandatory session breaks | After extended play periods | Spelinspektionen guidance |
| Stodlinjen link | Swedish gambling helpline | Best practice / expected |
| Responsible Gaming page | In Swedish | Language requirement |
| Swedish language site | Full site in Swedish | License condition |

**Key differences from UK/MGA:**
- Spelpaus is a NATIONAL registry (like GamStop but government-run)
- Bonus restrictions are the strictest in Europe
- Credit card payments will be banned from 2026
- Deposit limits are mandatory (not optional) for all players

---

## 5. Denmark (Spillemyndigheden) Licensing

### License Overview

- **Regulator**: Spillemyndigheden (Danish Gambling Authority)
- **License validity**: 5 years maximum
- **Application fee (2026)**: DKK 343,300 (online casino); DKK 480,600 (combined betting + casino)
- **Gaming tax**: 28% on GGR for online casino

### Key Requirements

1. Danish or EU-registered legal entity
2. Integration with ROFUS self-exclusion register
3. Game supplier licensing (separate license required from Jan 2025)
4. Technical requirements compliance (updated version published)
5. Danish language support
6. AML/KYC compliance with Danish AML Act

### ROFUS Self-Exclusion System

- **24-hour break**: Quick temporary exclusion
- **Temporary exclusion**: 1, 3, or 6 months
- **Permanent exclusion**: Indefinite (difficult to reverse)
- Operator must check ROFUS at registration and login
- Push notifications classified as gambling advertising; ROFUS must be consulted before sending
- Permanent exclusion triggers automatic account closure and fund payout

### MANDATORY Website Elements (Denmark)

| Element | Requirement | Legal Basis |
|---|---|---|
| Spillemyndigheden license display | On every page | Danish Gambling Act |
| 18+ age restriction | Prominently displayed | Danish Gambling Act |
| ROFUS link + integration | MANDATORY (national registry) | Danish Gambling Act |
| ROFUS check on registration | Must verify exclusion status | Technical requirements |
| ROFUS check before push notifications | Marketing restriction | Technical requirements (2025 update) |
| StopSpillet link | Danish gambling helpline | Expected |
| Responsible Gaming page | In Danish | License condition |
| Danish language site | Full site in Danish | License condition |
| Deposit limit tools | Must be available | Technical requirements |
| Self-exclusion (site-level) | In addition to ROFUS | Technical requirements |
| Session activity statement | On demand and periodic | Technical requirements |

**Key differences:**
- ROFUS is Denmark's national self-exclusion registry (comparable to GamStop/Spelpaus)
- Game suppliers now need separate licenses (from January 2025)
- Push notifications require ROFUS consultation (unique to Denmark)
- Higher tax rate (28%) than most other EU jurisdictions

---

## 6. Brazil (SPA-MF) — Summary Reference

(Detailed coverage in Chapter 46 of the book)

### Key Website Requirements

| Element | Requirement |
|---|---|
| SPA-MF license display | On every page |
| National self-exclusion registry | MANDATORY integration |
| Bolsa Familia / BPC welfare check | Must block welfare recipients from gambling |
| 30-minute geolocation reverification | Continuous location checks |
| SIGAP reporting | Regulatory reporting system |
| CPF verification | Brazilian tax ID verification at registration |
| Portuguese language | Full site in Brazilian Portuguese |
| Responsible gaming tools | Deposit limits, session limits, self-exclusion |

---

## 7. Cross-Jurisdiction Comparison: Website Footer Elements

### Universal Requirements (All Jurisdictions)

- License number/seal displayed on every page
- 18+ (or 21+ in some markets) age restriction symbol
- Responsible gaming page with tools and information
- Self-exclusion mechanism (operator-level minimum)
- Deposit limit tools
- Terms & Conditions
- Privacy Policy
- Links to local gambling help organizations

### Jurisdiction-Specific Requirements

| Element | UK | Malta | Sweden | Denmark | Brazil |
|---|---|---|---|---|---|
| National self-exclusion registry | GamStop | None (operator-level) | Spelpaus | ROFUS | National registry |
| Primary RG organization | BeGambleAware | RGF Malta | Stodlinjen | StopSpillet | - |
| Secondary RG organization | GamCare | BetBlocker/Gamban | - | - | - |
| Mandatory deposit limits | From Oct 2025 | Offered (player choice) | MANDATORY all players | Offered | Offered |
| Bonus restrictions | 10x max wagering (2026) | Standard | ONE bonus EVER | Standard | TBD |
| Language requirement | English | Any (English common) | Swedish MANDATORY | Danish MANDATORY | Portuguese MANDATORY |
| Session timer | RTS 13 | PPD | Mandatory | Required | Required |
| Financial vulnerability checks | GBP 150+/month | Via markers of harm | Via deposit limits | Standard CDD | Welfare check |
| Credit card ban | No (under review) | No | Coming 2026 | No | TBD |
| Cookie consent | PECR/GDPR | GDPR | Swedish GDPR equiv. | Danish GDPR equiv. | LGPD |

---

## 8. Implementation Notes for Casino Website Footer

### Recommended Footer Layout (Multi-Jurisdiction)

The footer should dynamically display compliance elements based on the player's jurisdiction:

**Top row**: Payment method logos (Visa, Mastercard, etc.)

**Middle row**: Responsible gaming logos
- UK visitors: BeGambleAware logo + GamCare logo + GamStop logo + 18+ badge
- Malta/EU visitors: RGF Malta logo + 18+ badge + MGA seal
- Sweden visitors: Spelpaus logo + Stodlinjen + 18+ badge + Spelinspektionen logo
- Denmark visitors: ROFUS logo + StopSpillet + 18+ badge + Spillemyndigheden logo

**Bottom row**: License text
- "AcmetoCasino is operated by [Company Name], registered in Malta (C XXXXX)."
- "Licensed and regulated by the Malta Gaming Authority (MGA/B2C/XXX/XXXX)."
- "Licensed and regulated in Great Britain by the Gambling Commission under licence number XXXXX."
- Links: Terms | Privacy | Responsible Gaming | Cookies | Complaints

**Legal disclaimer text**:
- "AcmetoCasino is committed to responsible gaming. Gambling can be addictive. Play responsibly."
- "Players must be 18+ to register. BeGambleAware.org"

---

## 9. Technical Compliance Costs Summary

| Item | UK (UKGC) | Malta (MGA) | Sweden | Denmark |
|---|---|---|---|---|
| Application fee | GBP 4,224-91,686 | EUR 5,000 | Included in annual | DKK 343,300-480,600 |
| Annual license fee | GBP 4,199-793,729 | EUR 15,000-25,000 | EUR 23,760 | Included in application |
| Gaming tax | 21% RGD + 15% GGY | 5% GGR (Malta players only) | 18% GGR | 28% GGR |
| ISO 27001 audit | GBP 10,000-25,000/yr | EUR 10,000-25,000/yr | Comparable | Comparable |
| Penetration test | GBP 5,000-15,000/yr | EUR 5,000-15,000/yr | Comparable | Comparable |
| RNG certification | GBP 15,000-50,000 | EUR 15,000-50,000 | Comparable | Comparable |
| GamStop integration | Included (API free) | N/A | N/A | N/A |
| Spelpaus integration | N/A | N/A | Included | N/A |
| ROFUS integration | N/A | N/A | N/A | Included |

---

## 10. Key Regulatory Trends (2025-2026)

1. **Affordability checks expanding**: UK leading with GBP 150/month threshold for financial vulnerability checks; other jurisdictions watching closely
2. **Bonus restrictions tightening**: UK capping wagering at 10x; Sweden already restricts to one bonus ever
3. **Credit card bans**: Sweden implementing in 2026; UK considering
4. **Mandatory deposit limits**: Moving from optional to mandatory across jurisdictions
5. **Self-exclusion centralization**: MGA working toward cross-operator system; UK, Sweden, Denmark already have national registries
6. **AI-driven player monitoring**: Regulators expecting more sophisticated behavioral detection systems
7. **Source of Funds automation**: KYC/AML checks becoming more technology-driven with biometric liveness and document verification
8. **Game supplier licensing**: Denmark now requires separate supplier licenses; trend likely to spread
9. **Responsible product design**: UKGC RTS 14 setting the standard for game design restrictions (spin speeds, autoplay limits)
10. **Gambling levies**: UK introduced mandatory levy in April 2025; funding model for treatment and research
