#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 09, Legal Framework and Contracts.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Legal Contract Template Generator for iGaming Operators.

Generates structured contract templates for:
  - Game Provider Agreements (content supply, integration, revenue share)
  - Payment Processor Agreements (PSP, acquirer, e-wallet)
  - Affiliate/Marketing Agreements (CPA, revenue share, hybrid)
  - Data Processing Agreements (DPA per GDPR Art.28)
  - Platform License Agreements (white-label, turnkey)
  - KYC/AML Provider Agreements

Templates include jurisdiction-specific clauses for:
  MGA, UKGC, Curacao, Gibraltar, Sweden (SGA), Brazil (SPA)

Output: JSON structure, Markdown, or plain text (adaptable to DOCX via python-docx).

Usage:
    python template_generator.py --type game_provider --jurisdiction mga --format markdown
    python template_generator.py --type payment_processor --jurisdiction ukgc
    python template_generator.py --type affiliate --jurisdiction mga --format json
    python template_generator.py --demo
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Template clause library
# ---------------------------------------------------------------------------

@dataclass
class ContractClause:
    section_number: str
    title: str
    body: str
    jurisdiction_notes: dict = field(default_factory=dict)
    is_mandatory: bool = True
    regulatory_reference: str = ""


class ClauseLibrary:
    """Centralized clause library for iGaming contracts."""

    @staticmethod
    def definitions() -> ContractClause:
        return ContractClause(
            section_number="1",
            title="DEFINITIONS AND INTERPRETATION",
            body="""1.1 In this Agreement, unless the context otherwise requires:

"Affiliate" means any entity that directly or indirectly controls, is controlled by,
or is under common control with a Party.

"Confidential Information" means all information disclosed by one Party to the other,
whether orally, in writing, or electronically, that is designated as confidential or
that reasonably should be understood to be confidential.

"Effective Date" means the date specified in Schedule 1.

"Gaming Content" means the games, software, and related assets provided under this Agreement.

"Gross Gaming Revenue" (GGR) means the total amount wagered by Players minus the total
amount paid out as winnings, before any deductions.

"Net Gaming Revenue" (NGR) means GGR minus bonuses, free spins costs, jackpot contributions,
chargebacks, and any applicable gaming taxes.

"Player" means any individual who uses the Operator's Platform to access Gaming Content.

"Platform" means the Operator's online gaming platform, including all websites, mobile
applications, and APIs.

"Regulatory Authority" means the relevant gambling regulatory body in each Licensed Territory.

"Territory" means the jurisdictions listed in Schedule 2.""",
            is_mandatory=True,
        )

    @staticmethod
    def term_and_termination() -> ContractClause:
        return ContractClause(
            section_number="3",
            title="TERM AND TERMINATION",
            body="""3.1 This Agreement shall commence on the Effective Date and continue for an
initial term of [INITIAL_TERM] months ("Initial Term"), unless terminated earlier
in accordance with this clause.

3.2 Following the Initial Term, this Agreement shall automatically renew for
successive periods of [RENEWAL_TERM] months ("Renewal Term"), unless either Party
gives written notice of non-renewal at least [NOTICE_PERIOD] days prior to the
end of the then-current term.

3.3 Either Party may terminate this Agreement immediately by written notice if:
    (a) the other Party commits a material breach and fails to remedy such breach
        within 30 days of receiving written notice;
    (b) the other Party becomes insolvent, enters administration, or has a receiver appointed;
    (c) any Regulatory Authority revokes, suspends, or materially restricts any license
        required for the performance of this Agreement;
    (d) a Force Majeure event continues for more than 90 consecutive days.

3.4 The Operator may terminate this Agreement immediately if:
    (a) the Provider's gaming content fails to meet the certified RTP specifications
        for three consecutive calendar months;
    (b) the Provider's platform availability falls below the SLA threshold for two
        consecutive calendar months;
    (c) the Provider is found to be in violation of applicable anti-money laundering regulations.

3.5 Upon termination:
    (a) all outstanding settlement amounts shall be paid within [FINAL_SETTLEMENT_DAYS] days;
    (b) all Player data shall be handled in accordance with the Data Processing Agreement;
    (c) the Provider shall continue to support the resolution of any pending game rounds
        for a period of 30 days;
    (d) all Confidential Information shall be returned or destroyed.""",
            jurisdiction_notes={
                "ukgc": "Add: UKGC notification requirement within 5 business days of termination.",
                "mga": "Add: MGA must be notified of any change in key supplier relationships.",
            },
            is_mandatory=True,
        )

    @staticmethod
    def financial_terms_revenue_share() -> ContractClause:
        return ContractClause(
            section_number="4",
            title="FINANCIAL TERMS - REVENUE SHARE",
            body="""4.1 Revenue Share Calculation:
    The Operator shall pay the Provider [REVENUE_SHARE_PCT]% of Net Gaming Revenue
    generated from the Provider's Gaming Content during each Settlement Period.

4.2 Minimum Guarantee:
    Notwithstanding clause 4.1, the minimum monthly payment shall not be less than
    [MINIMUM_GUARANTEE] [CURRENCY] ("Minimum Guarantee").

4.3 Settlement Period:
    Settlements shall be calculated and paid [SETTLEMENT_FREQUENCY] (the "Settlement Period").

4.4 Payment Terms:
    All amounts due shall be paid within [PAYMENT_TERMS] days of the end of
    each Settlement Period by wire transfer to the account specified in Schedule 3.

4.5 Reporting:
    The Operator shall provide the Provider with a detailed revenue report within
    [REPORTING_DAYS] business days of the end of each Settlement Period, including:
    (a) total bets and wins per game;
    (b) bonus costs allocated to Provider's content;
    (c) jackpot contributions;
    (d) NGR calculation breakdown;
    (e) currency conversion rates applied.

4.6 Audit Rights:
    Each Party shall have the right to audit the other Party's records relating to
    this Agreement once per calendar year, with 30 days' prior written notice.
    Audits shall be conducted by an independent auditor at the requesting Party's expense.""",
            is_mandatory=True,
        )

    @staticmethod
    def data_protection() -> ContractClause:
        return ContractClause(
            section_number="7",
            title="DATA PROTECTION AND PRIVACY",
            body="""7.1 Each Party shall comply with all applicable data protection laws, including
the General Data Protection Regulation (EU) 2016/679 ("GDPR") and the UK Data
Protection Act 2018, as applicable.

7.2 The Parties acknowledge that the Operator is the Data Controller and the Provider
is the Data Processor in respect of Player personal data processed under this Agreement.

7.3 The Provider shall:
    (a) process Player personal data only in accordance with the Operator's documented
        instructions as set out in the Data Processing Agreement (Schedule 5);
    (b) implement appropriate technical and organizational measures to ensure a level
        of security appropriate to the risk, including encryption of data in transit
        and at rest;
    (c) not transfer Player personal data outside the EEA without appropriate safeguards;
    (d) assist the Operator in responding to data subject access requests within
        the statutory timeframes;
    (e) notify the Operator without undue delay (and in any event within 24 hours)
        upon becoming aware of a personal data breach;
    (f) delete or return all Player personal data upon termination of this Agreement.

7.4 Sub-processing:
    The Provider shall not engage any sub-processor without the prior written consent
    of the Operator. A list of approved sub-processors is set out in Schedule 5.""",
            jurisdiction_notes={
                "ukgc": "Must comply with ICO guidance on gambling data processing.",
                "brazil_spa": "Must comply with LGPD (Lei Geral de Protecao de Dados).",
                "mga": "MGA Player Protection Directive requires enhanced data safeguards.",
            },
            regulatory_reference="GDPR Art.28, Art.32, Art.33",
            is_mandatory=True,
        )

    @staticmethod
    def responsible_gambling() -> ContractClause:
        return ContractClause(
            section_number="8",
            title="RESPONSIBLE GAMBLING",
            body="""8.1 The Provider shall ensure that all Gaming Content complies with applicable
responsible gambling requirements, including:
    (a) display of responsible gambling messages during gameplay;
    (b) support for session time limits and reality checks;
    (c) integration with the Operator's self-exclusion systems;
    (d) no autoplay features that circumvent responsible gambling controls;
    (e) clear display of odds, RTP, and volatility information.

8.2 The Provider shall promptly implement any changes to Gaming Content required by
the Operator to comply with regulatory requirements related to responsible gambling.

8.3 The Provider's Gaming Content shall support the following operator-initiated controls:
    (a) stake limits;
    (b) loss limits;
    (c) deposit limits (where applicable);
    (d) cooling-off periods;
    (e) game-specific exclusions.""",
            jurisdiction_notes={
                "ukgc": "Must comply with LCCP Social Responsibility Code Provisions. "
                        "No reverse withdrawals. Speed of play limits on slots.",
                "sga": "Spelpaus integration mandatory. No bonuses for Swedish players.",
                "mga": "Player Protection Directive compliance required.",
            },
            is_mandatory=True,
        )

    @staticmethod
    def intellectual_property() -> ContractClause:
        return ContractClause(
            section_number="9",
            title="INTELLECTUAL PROPERTY",
            body="""9.1 All intellectual property rights in the Gaming Content shall remain the
exclusive property of the Provider.

9.2 The Provider grants to the Operator a non-exclusive, non-transferable,
non-sublicensable license to use, display, and distribute the Gaming Content on the
Platform within the Territory during the Term of this Agreement.

9.3 The Operator grants to the Provider a non-exclusive license to use the Operator's
trademarks and branding solely for the purpose of customizing the Gaming Content
for the Platform.

9.4 Neither Party shall:
    (a) reverse engineer, decompile, or disassemble the other Party's software;
    (b) use the other Party's intellectual property except as expressly permitted;
    (c) register or attempt to register any intellectual property rights that are
        confusingly similar to the other Party's marks.

9.5 The Provider warrants that the Gaming Content does not infringe any third-party
intellectual property rights and shall indemnify the Operator against any claims
arising from such infringement.""",
            is_mandatory=True,
        )

    @staticmethod
    def sla_clause() -> ContractClause:
        return ContractClause(
            section_number="6",
            title="SERVICE LEVELS AND PERFORMANCE",
            body="""6.1 The Provider shall maintain the following service levels:
    (a) Platform Availability: [UPTIME_SLA]% measured monthly, excluding scheduled
        maintenance windows;
    (b) API Response Time: P95 latency not exceeding [P95_LATENCY]ms;
    (c) Game Round Resolution: 99.9% of game rounds resolved within [ROUND_RESOLUTION] seconds;
    (d) Transaction Success Rate: not less than [TX_SUCCESS_RATE]% per calendar month.

6.2 Scheduled maintenance shall be performed during low-traffic windows (02:00-06:00 CET)
with at least 48 hours' prior notice.

6.3 Service Level Credits:
    If the Provider fails to meet any service level for a calendar month, the Operator
    shall be entitled to a service credit calculated as follows:
    (a) Availability 99.0-[UPTIME_SLA]%: [PENALTY_MINOR]% of monthly fees;
    (b) Availability 98.0-99.0%: [PENALTY_MAJOR]% of monthly fees;
    (c) Availability below 98.0%: [PENALTY_CRITICAL]% of monthly fees;
    (d) Maximum aggregate service credits: 30% of monthly fees.

6.4 The Provider shall provide real-time status monitoring via an API endpoint and
notify the Operator within 15 minutes of any service degradation.""",
            is_mandatory=True,
        )

    @staticmethod
    def compliance_clause() -> ContractClause:
        return ContractClause(
            section_number="5",
            title="REGULATORY COMPLIANCE",
            body="""5.1 The Provider warrants that:
    (a) it holds all necessary licenses and certifications required to provide Gaming
        Content in each Territory;
    (b) all Gaming Content has been tested and certified by an approved testing laboratory;
    (c) it shall maintain such licenses and certifications throughout the Term.

5.2 The Provider shall:
    (a) provide copies of all gaming licenses and test certificates upon request;
    (b) notify the Operator immediately of any regulatory action, investigation,
        or material change in licensing status;
    (c) cooperate fully with any regulatory audit or investigation;
    (d) comply with all applicable anti-money laundering regulations;
    (e) ensure all RNG (Random Number Generator) implementations are certified
        and regularly re-tested.

5.3 If any Regulatory Authority requires changes to Gaming Content, the Provider
shall implement such changes within the timeframe specified by the authority, or
within 30 days if no timeframe is specified.

5.4 The Operator reserves the right to suspend the Provider's Gaming Content
immediately if there is a reasonable belief of regulatory non-compliance.""",
            jurisdiction_notes={
                "ukgc": "Provider must hold UKGC license or supply via licensed host. "
                        "Annual RTP verification required.",
                "mga": "B2B license under MGA framework required. GLI/BMM certification.",
                "curacao": "Sub-license under master license holder required.",
                "brazil_spa": "SPA approval required. Content must support Portuguese (BR).",
            },
            is_mandatory=True,
        )


# ---------------------------------------------------------------------------
# Template generator
# ---------------------------------------------------------------------------

TEMPLATE_CONFIGS = {
    "game_provider": {
        "title": "GAME CONTENT SUPPLY AND INTEGRATION AGREEMENT",
        "clauses": [
            "definitions", "recitals_game_provider", "term_and_termination",
            "financial_terms_revenue_share", "compliance_clause", "sla_clause",
            "data_protection", "responsible_gambling", "intellectual_property",
            "confidentiality", "limitation_of_liability", "force_majeure",
            "governing_law", "dispute_resolution", "general_provisions",
        ],
        "schedules": [
            "Schedule 1: Key Commercial Terms",
            "Schedule 2: Licensed Territories",
            "Schedule 3: Payment Details",
            "Schedule 4: Service Level Agreement",
            "Schedule 5: Data Processing Agreement",
            "Schedule 6: Game Content List and RTP Specifications",
            "Schedule 7: Integration Technical Specifications",
        ],
    },
    "payment_processor": {
        "title": "PAYMENT PROCESSING SERVICES AGREEMENT",
        "clauses": [
            "definitions", "recitals_payment", "term_and_termination",
            "financial_terms_payment", "compliance_clause", "sla_clause",
            "data_protection", "pci_dss", "aml_kyc",
            "confidentiality", "limitation_of_liability", "force_majeure",
            "governing_law", "dispute_resolution", "general_provisions",
        ],
        "schedules": [
            "Schedule 1: Key Commercial Terms",
            "Schedule 2: Supported Payment Methods and Currencies",
            "Schedule 3: Fee Schedule",
            "Schedule 4: Service Level Agreement",
            "Schedule 5: Data Processing Agreement",
            "Schedule 6: PCI DSS Compliance Certificate",
            "Schedule 7: Chargeback and Fraud Management Procedures",
        ],
    },
    "affiliate": {
        "title": "AFFILIATE MARKETING AGREEMENT",
        "clauses": [
            "definitions", "recitals_affiliate", "term_and_termination",
            "financial_terms_affiliate", "compliance_clause",
            "marketing_standards", "data_protection", "intellectual_property",
            "confidentiality", "limitation_of_liability",
            "governing_law", "dispute_resolution", "general_provisions",
        ],
        "schedules": [
            "Schedule 1: Commission Structure",
            "Schedule 2: Approved Marketing Channels",
            "Schedule 3: Brand Guidelines",
            "Schedule 4: Prohibited Marketing Practices",
            "Schedule 5: Data Processing Agreement",
        ],
    },
    "data_processing": {
        "title": "DATA PROCESSING AGREEMENT (GDPR Art.28)",
        "clauses": [
            "definitions_dpa", "scope_and_purpose", "processor_obligations",
            "sub_processing", "data_subject_rights", "security_measures",
            "breach_notification", "data_transfers", "audit_rights",
            "return_and_deletion", "liability",
        ],
        "schedules": [
            "Schedule 1: Description of Processing",
            "Schedule 2: Technical and Organizational Measures",
            "Schedule 3: List of Sub-processors",
            "Schedule 4: Standard Contractual Clauses (if applicable)",
        ],
    },
}


class TemplateGenerator:

    def __init__(self):
        self.clause_library = ClauseLibrary()

    def generate(self, contract_type: str, jurisdiction: str,
                 variables: Optional[dict] = None) -> dict:
        config = TEMPLATE_CONFIGS.get(contract_type)
        if not config:
            raise ValueError(f"Unknown contract type: {contract_type}. "
                             f"Available: {list(TEMPLATE_CONFIGS.keys())}")

        vars_defaults = self._default_variables(contract_type, jurisdiction)
        if variables:
            vars_defaults.update(variables)

        # Build clause list
        clauses = []
        clause_methods = {
            "definitions": self.clause_library.definitions,
            "term_and_termination": self.clause_library.term_and_termination,
            "financial_terms_revenue_share": self.clause_library.financial_terms_revenue_share,
            "compliance_clause": self.clause_library.compliance_clause,
            "sla_clause": self.clause_library.sla_clause,
            "data_protection": self.clause_library.data_protection,
            "responsible_gambling": self.clause_library.responsible_gambling,
            "intellectual_property": self.clause_library.intellectual_property,
        }

        section_num = 1
        for clause_key in config["clauses"]:
            method = clause_methods.get(clause_key)
            if method:
                clause = method()
                clause.section_number = str(section_num)
                # Apply variable substitution
                for var_key, var_val in vars_defaults.items():
                    clause.body = clause.body.replace(f"[{var_key}]", str(var_val))
                clauses.append(clause)
            else:
                # Stub for clauses not yet in library
                clauses.append(ContractClause(
                    section_number=str(section_num),
                    title=clause_key.upper().replace("_", " "),
                    body=f"[{clause_key} - clause to be drafted by legal counsel]",
                ))
            section_num += 1

        # Add jurisdiction-specific notes
        jurisdiction_notes = []
        for clause in clauses:
            if jurisdiction in clause.jurisdiction_notes:
                jurisdiction_notes.append({
                    "section": clause.section_number,
                    "title": clause.title,
                    "note": clause.jurisdiction_notes[jurisdiction],
                })

        return {
            "contract_type": contract_type,
            "title": config["title"],
            "jurisdiction": jurisdiction,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "variables": vars_defaults,
            "clauses": [
                {
                    "section": c.section_number,
                    "title": c.title,
                    "body": c.body,
                    "mandatory": c.is_mandatory,
                    "regulatory_reference": c.regulatory_reference,
                }
                for c in clauses
            ],
            "schedules": config["schedules"],
            "jurisdiction_specific_notes": jurisdiction_notes,
            "disclaimer": (
                "TEMPLATE ONLY - This document is a template for reference purposes. "
                "All contracts must be reviewed and approved by qualified legal counsel "
                "before execution. Specific terms must be negotiated between the parties."
            ),
        }

    def _default_variables(self, contract_type: str, jurisdiction: str) -> dict:
        base = {
            "INITIAL_TERM": 24,
            "RENEWAL_TERM": 12,
            "NOTICE_PERIOD": 90,
            "FINAL_SETTLEMENT_DAYS": 45,
            "PAYMENT_TERMS": 30,
            "REPORTING_DAYS": 5,
            "UPTIME_SLA": 99.95,
            "P95_LATENCY": 200,
            "ROUND_RESOLUTION": 30,
            "TX_SUCCESS_RATE": 99.9,
            "PENALTY_MINOR": 2,
            "PENALTY_MAJOR": 5,
            "PENALTY_CRITICAL": 10,
        }

        type_vars = {
            "game_provider": {
                "REVENUE_SHARE_PCT": 12,
                "MINIMUM_GUARANTEE": "5,000",
                "CURRENCY": "EUR",
                "SETTLEMENT_FREQUENCY": "monthly",
            },
            "payment_processor": {
                "TRANSACTION_FEE_PCT": 1.5,
                "FIXED_FEE_PER_TX": 0.10,
                "MONTHLY_MINIMUM": "2,500",
                "CHARGEBACK_FEE": 25,
                "ROLLING_RESERVE_PCT": 5,
                "ROLLING_RESERVE_DAYS": 180,
            },
            "affiliate": {
                "CPA_AMOUNT": 150,
                "REVENUE_SHARE_PCT": 25,
                "NEGATIVE_CARRYOVER": "Yes",
                "COOKIE_DURATION_DAYS": 30,
            },
        }

        jurisdiction_vars = {
            "ukgc": {"GOVERNING_LAW": "England and Wales", "CURRENCY": "GBP"},
            "mga": {"GOVERNING_LAW": "Malta", "CURRENCY": "EUR"},
            "curacao": {"GOVERNING_LAW": "Curacao", "CURRENCY": "USD"},
            "gibraltar": {"GOVERNING_LAW": "Gibraltar", "CURRENCY": "GBP"},
            "sga": {"GOVERNING_LAW": "Sweden", "CURRENCY": "SEK"},
            "brazil_spa": {"GOVERNING_LAW": "Brazil", "CURRENCY": "BRL"},
        }

        base.update(type_vars.get(contract_type, {}))
        base.update(jurisdiction_vars.get(jurisdiction, {}))  # ty:ignore[no-matching-overload]
        return base

    def to_markdown(self, template: dict) -> str:
        lines = []
        lines.append(f"# {template['title']}")
        lines.append(f"\n**Jurisdiction:** {template['jurisdiction'].upper()}")
        lines.append(f"**Generated:** {template['generated_at'][:10]}")
        lines.append(f"\n---\n")
        lines.append(f"*{template['disclaimer']}*")
        lines.append(f"\n---\n")

        for clause in template["clauses"]:
            lines.append(f"\n## {clause['section']}. {clause['title']}\n")
            lines.append(clause["body"])
            if clause.get("regulatory_reference"):
                lines.append(f"\n> *Regulatory Reference: {clause['regulatory_reference']}*")

        if template.get("jurisdiction_specific_notes"):
            lines.append(f"\n---\n")
            lines.append(f"## JURISDICTION-SPECIFIC NOTES ({template['jurisdiction'].upper()})\n")
            for note in template["jurisdiction_specific_notes"]:
                lines.append(f"- **Section {note['section']} ({note['title']}):** {note['note']}")

        lines.append(f"\n---\n")
        lines.append("## SCHEDULES\n")
        for schedule in template["schedules"]:
            lines.append(f"- {schedule}")

        lines.append(f"\n---\n")
        lines.append("## SIGNATURE BLOCK\n")
        lines.append("| | Operator | Provider |")
        lines.append("|---|---|---|")
        lines.append("| **Company Name** | [________________] | [________________] |")
        lines.append("| **Authorized Signatory** | [________________] | [________________] |")
        lines.append("| **Title** | [________________] | [________________] |")
        lines.append("| **Date** | [________________] | [________________] |")
        lines.append("| **Signature** | [________________] | [________________] |")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Demo & CLI
# ---------------------------------------------------------------------------

def demo():
    generator = TemplateGenerator()

    templates_to_generate = [
        ("game_provider", "mga"),
        ("payment_processor", "ukgc"),
        ("affiliate", "mga"),
    ]

    for ctype, jurisdiction in templates_to_generate:
        template = generator.generate(ctype, jurisdiction)
        md = generator.to_markdown(template)

        print("=" * 80)
        print(f"GENERATED: {template['title']}")
        print(f"Jurisdiction: {jurisdiction.upper()}")
        print(f"Clauses: {len(template['clauses'])}")
        print(f"Schedules: {len(template['schedules'])}")
        print(f"Jurisdiction Notes: {len(template['jurisdiction_specific_notes'])}")
        print(f"Markdown length: {len(md)} chars")
        print("=" * 80)

    # Print one full template as markdown
    template = generator.generate("game_provider", "mga")
    md = generator.to_markdown(template)
    print("\n" + md[:3000] + "\n\n[... truncated for demo ...]\n")

    print("[OK] Template generator demo complete.")
    print(f"     Available types: {list(TEMPLATE_CONFIGS.keys())}")
    print(f"     Available jurisdictions: mga, ukgc, curacao, gibraltar, sga, brazil_spa")


def main():
    parser = argparse.ArgumentParser(description="Legal Contract Template Generator")
    parser.add_argument("--type", choices=list(TEMPLATE_CONFIGS.keys()), default=None)
    parser.add_argument("--jurisdiction", default="mga")
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="markdown")
    parser.add_argument("--output", default=None, help="Output file path")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo or not args.type:
        demo()
        return

    generator = TemplateGenerator()
    template = generator.generate(args.type, args.jurisdiction)

    if args.format == "json":
        output = json.dumps(template, indent=2)
    else:
        output = generator.to_markdown(template)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[+] Template written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
