#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
License Application Document Checklist Generator
===================================================

Generates jurisdiction-specific document checklists for iGaming license
applications. Covers corporate documents, personal declarations, technical
documentation, financial requirements, and compliance materials.

Usage:
    python license_doc_generator.py --jurisdiction MGA
    python license_doc_generator.py --jurisdiction UKGC --format json
    python license_doc_generator.py --all --export checklist.csv
"""

import json
import csv
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class DocumentItem:
    """A single document required for a license application."""
    id: str
    name: str
    category: str          # corporate, personal, technical, financial, compliance, operational
    description: str
    mandatory: bool = True
    apostille_required: bool = False
    notarization_required: bool = False
    max_age_months: int = 0   # 0 = no expiry, e.g. 3 = must be less than 3 months old
    format_notes: str = ""
    typical_lead_time_days: int = 7
    responsible_party: str = "applicant"  # applicant, legal_counsel, auditor, technical_team
    status: str = "pending"  # pending, in_progress, obtained, submitted, approved
    notes: str = ""


# ---------------------------------------------------------------------------
# Jurisdiction-specific document requirements
# ---------------------------------------------------------------------------

def _mga_documents() -> list[DocumentItem]:
    """Malta Gaming Authority application documents."""
    return [
        # Corporate documents
        DocumentItem("MGA-C01", "Certificate of Incorporation", "corporate",
                      "Certified copy of company registration certificate",
                      apostille_required=True, typical_lead_time_days=14),
        DocumentItem("MGA-C02", "Memorandum and Articles of Association", "corporate",
                      "Latest version showing company structure and governance",
                      apostille_required=True),
        DocumentItem("MGA-C03", "Certificate of Good Standing", "corporate",
                      "Issued by home jurisdiction registrar",
                      max_age_months=3, apostille_required=True, typical_lead_time_days=14),
        DocumentItem("MGA-C04", "Shareholders Register", "corporate",
                      "Complete register showing all shareholders and beneficial owners"),
        DocumentItem("MGA-C05", "Directors Register", "corporate",
                      "List of all current directors with appointment dates"),
        DocumentItem("MGA-C06", "Group Structure Chart", "corporate",
                      "Organizational chart showing all entities in the group up to UBO",
                      responsible_party="legal_counsel"),
        DocumentItem("MGA-C07", "Board Resolutions", "corporate",
                      "Board resolution authorizing the license application",
                      notarization_required=True),
        DocumentItem("MGA-C08", "Registered Office Proof", "corporate",
                      "Malta registered office lease agreement or ownership documents"),

        # Personal declarations (for each key person)
        DocumentItem("MGA-P01", "Personal Declaration Form", "personal",
                      "MGA Personal Declaration Form for each director, UBO, and key function holder",
                      typical_lead_time_days=21),
        DocumentItem("MGA-P02", "Passport Copy (Certified)", "personal",
                      "Certified copy of valid passport for each key person",
                      notarization_required=True),
        DocumentItem("MGA-P03", "Proof of Address", "personal",
                      "Utility bill or bank statement for each key person",
                      max_age_months=3),
        DocumentItem("MGA-P04", "Police Conduct Certificate", "personal",
                      "Criminal record check from country of residence and nationality",
                      max_age_months=6, apostille_required=True, typical_lead_time_days=30),
        DocumentItem("MGA-P05", "Curriculum Vitae", "personal",
                      "Detailed CV for each key person covering last 10 years"),
        DocumentItem("MGA-P06", "Source of Wealth Declaration", "personal",
                      "Evidence of legitimate source of wealth for UBOs",
                      typical_lead_time_days=21),
        DocumentItem("MGA-P07", "Tax Compliance Certificate", "personal",
                      "Tax clearance from country of tax residence for each key person",
                      max_age_months=6, typical_lead_time_days=21),

        # Technical documentation
        DocumentItem("MGA-T01", "System Architecture Document", "technical",
                      "Complete technical architecture including network diagrams, data flows",
                      responsible_party="technical_team", typical_lead_time_days=30),
        DocumentItem("MGA-T02", "RNG Certificate", "technical",
                      "Random Number Generator certification from approved testing lab",
                      responsible_party="technical_team", typical_lead_time_days=60),
        DocumentItem("MGA-T03", "Game Mathematics Reports", "technical",
                      "Mathematical verification of all game RTP and volatility",
                      responsible_party="technical_team", typical_lead_time_days=45),
        DocumentItem("MGA-T04", "Information Security Policy", "technical",
                      "ISO 27001 aligned information security management system documentation",
                      responsible_party="technical_team"),
        DocumentItem("MGA-T05", "Business Continuity Plan", "technical",
                      "DR/BCP procedures including RTO, RPO, and testing evidence",
                      responsible_party="technical_team"),
        DocumentItem("MGA-T06", "Penetration Test Report", "technical",
                      "External penetration test by approved firm (less than 12 months old)",
                      max_age_months=12, responsible_party="technical_team", typical_lead_time_days=30),
        DocumentItem("MGA-T07", "Source Code Escrow Agreement", "technical",
                      "Escrow agreement with approved escrow agent for platform source code",
                      responsible_party="legal_counsel", typical_lead_time_days=30),
        DocumentItem("MGA-T08", "Data Protection Impact Assessment", "technical",
                      "DPIA covering player data processing under GDPR",
                      responsible_party="technical_team"),

        # Financial documents
        DocumentItem("MGA-F01", "Audited Financial Statements", "financial",
                      "Last 3 years audited financials (or business plan if new entity)",
                      responsible_party="auditor", typical_lead_time_days=30),
        DocumentItem("MGA-F02", "Business Plan", "financial",
                      "5-year business plan with financial projections, marketing strategy",
                      typical_lead_time_days=30),
        DocumentItem("MGA-F03", "Bank Reference Letter", "financial",
                      "Reference from primary bank confirming good standing",
                      max_age_months=3, typical_lead_time_days=14),
        DocumentItem("MGA-F04", "Proof of Capital", "financial",
                      "Evidence of minimum capital requirement (EUR 100,000)",
                      max_age_months=1),
        DocumentItem("MGA-F05", "Player Funds Segregation Plan", "financial",
                      "Description of how player funds will be held and segregated"),
        DocumentItem("MGA-F06", "Insurance Certificate", "financial",
                      "Professional indemnity insurance covering gambling operations",
                      typical_lead_time_days=21),

        # Compliance documents
        DocumentItem("MGA-X01", "AML/CFT Policy", "compliance",
                      "Comprehensive AML/CFT procedures aligned with FIAU guidelines",
                      responsible_party="legal_counsel", typical_lead_time_days=21),
        DocumentItem("MGA-X02", "Responsible Gaming Policy", "compliance",
                      "Player protection measures: self-exclusion, limits, reality checks"),
        DocumentItem("MGA-X03", "Complaints Procedure", "compliance",
                      "Player complaints handling procedure with ADR mechanism"),
        DocumentItem("MGA-X04", "Terms and Conditions (Draft)", "compliance",
                      "Draft player-facing T&Cs compliant with MGA requirements",
                      responsible_party="legal_counsel"),
        DocumentItem("MGA-X05", "Privacy Policy (Draft)", "compliance",
                      "GDPR-compliant privacy notice for players",
                      responsible_party="legal_counsel"),
        DocumentItem("MGA-X06", "MLRO Appointment", "compliance",
                      "Appointment of Money Laundering Reporting Officer with qualifications",
                      typical_lead_time_days=30),
        DocumentItem("MGA-X07", "Risk Assessment", "compliance",
                      "Business Risk Assessment covering ML/TF and player protection risks",
                      responsible_party="legal_counsel"),
    ]


def _ukgc_documents() -> list[DocumentItem]:
    """UK Gambling Commission application documents."""
    return [
        DocumentItem("UKGC-C01", "Operating Licence Application Form", "corporate",
                      "Completed UKGC operating licence application form"),
        DocumentItem("UKGC-C02", "Certificate of Incorporation", "corporate",
                      "Companies House registration certificate"),
        DocumentItem("UKGC-C03", "Corporate Structure Chart", "corporate",
                      "Full group structure including all shareholders >3%"),
        DocumentItem("UKGC-C04", "Shareholders Agreement", "corporate",
                      "Any shareholder agreements in force", mandatory=False),
        DocumentItem("UKGC-P01", "Personal Management Licence Application", "personal",
                      "PML application for each qualifying position holder",
                      typical_lead_time_days=30),
        DocumentItem("UKGC-P02", "DBS Enhanced Check", "personal",
                      "Enhanced Disclosure and Barring Service check for each PML applicant",
                      typical_lead_time_days=30),
        DocumentItem("UKGC-P03", "Personal Financial Declaration", "personal",
                      "3 years of personal finances for each PML applicant"),
        DocumentItem("UKGC-T01", "Gambling Software Details", "technical",
                      "Full details of all gambling software including testing certificates",
                      responsible_party="technical_team"),
        DocumentItem("UKGC-T02", "Remote Technical Standards Compliance", "technical",
                      "Self-assessment against UKGC RTS requirements",
                      responsible_party="technical_team", typical_lead_time_days=30),
        DocumentItem("UKGC-T03", "GAMSTOP Integration Plan", "technical",
                      "Plan for integrating national self-exclusion scheme",
                      responsible_party="technical_team"),
        DocumentItem("UKGC-T04", "GamProtect Compliance", "technical",
                      "Affordability checks and customer interaction procedures",
                      responsible_party="technical_team"),
        DocumentItem("UKGC-F01", "Financial Plan", "financial",
                      "3-year financial forecasts demonstrating commercial viability"),
        DocumentItem("UKGC-F02", "Audited Accounts", "financial",
                      "Latest audited financial statements",
                      responsible_party="auditor"),
        DocumentItem("UKGC-F03", "Regulatory Settlement Plan", "financial",
                      "How regulatory settlements and player claims will be funded"),
        DocumentItem("UKGC-X01", "AML/CTF Policies and Procedures", "compliance",
                      "Comprehensive AML policies per ML Regulations 2017 and POCA 2002",
                      responsible_party="legal_counsel"),
        DocumentItem("UKGC-X02", "Social Responsibility Policies", "compliance",
                      "Customer interaction, self-exclusion, marketing, age verification"),
        DocumentItem("UKGC-X03", "Complaints Procedure", "compliance",
                      "IBAS or equivalent ADR scheme membership"),
        DocumentItem("UKGC-X04", "Fair and Open Policy", "compliance",
                      "How games are fair, open, and transparent"),
        DocumentItem("UKGC-X05", "Marketing Code Compliance", "compliance",
                      "ASA/CAP Code compliance for gambling advertising",
                      responsible_party="legal_counsel"),
    ]


def _curacao_documents() -> list[DocumentItem]:
    """Curaçao Gaming Control Board documents (new regime 2024+)."""
    return [
        DocumentItem("CUR-C01", "Application Form", "corporate",
                      "GCB prescribed application form"),
        DocumentItem("CUR-C02", "Certificate of Incorporation", "corporate",
                      "Curaçao Chamber of Commerce registration",
                      apostille_required=True),
        DocumentItem("CUR-C03", "Articles of Association", "corporate",
                      "Current articles of the Curaçao entity",
                      apostille_required=True),
        DocumentItem("CUR-C04", "Beneficial Ownership Declaration", "corporate",
                      "Full UBO disclosure to 25% threshold"),
        DocumentItem("CUR-P01", "Key Person Declaration", "personal",
                      "Background declaration for directors and UBOs",
                      typical_lead_time_days=14),
        DocumentItem("CUR-P02", "Police Clearance", "personal",
                      "Criminal background check from country of residence",
                      max_age_months=6, typical_lead_time_days=30),
        DocumentItem("CUR-P03", "Passport Copy", "personal",
                      "Certified passport copy for all key persons"),
        DocumentItem("CUR-T01", "Technical Standards Compliance", "technical",
                      "Self-assessment against GCB technical standards",
                      responsible_party="technical_team"),
        DocumentItem("CUR-T02", "RNG Testing Certificate", "technical",
                      "RNG certification from approved lab",
                      responsible_party="technical_team", typical_lead_time_days=45),
        DocumentItem("CUR-T03", "Security Assessment", "technical",
                      "IT security assessment or penetration test report",
                      responsible_party="technical_team"),
        DocumentItem("CUR-F01", "Business Plan", "financial",
                      "Business plan with 3-year financial projections"),
        DocumentItem("CUR-F02", "Proof of Funds", "financial",
                      "Bank statements showing sufficient capital",
                      max_age_months=3),
        DocumentItem("CUR-X01", "AML/CFT Policy", "compliance",
                      "AML procedures compliant with National Ordinance",
                      responsible_party="legal_counsel"),
        DocumentItem("CUR-X02", "Responsible Gaming Policy", "compliance",
                      "Player protection procedures and self-exclusion"),
        DocumentItem("CUR-X03", "Privacy Policy", "compliance",
                      "Data protection policy compliant with local requirements"),
    ]


JURISDICTION_DOCS = {
    "MGA": ("Malta Gaming Authority", _mga_documents),
    "UKGC": ("UK Gambling Commission", _ukgc_documents),
    "CUR": ("Curaçao Gaming Control Board", _curacao_documents),
}


# ---------------------------------------------------------------------------
# Checklist generator
# ---------------------------------------------------------------------------

class LicenseDocGenerator:
    """Generate and track license application document checklists."""

    def __init__(self):
        self.checklists: dict[str, list[DocumentItem]] = {}

    def generate_checklist(self, jurisdiction_code: str) -> dict:
        """Generate a complete document checklist for a jurisdiction."""
        code = jurisdiction_code.upper()
        if code not in JURISDICTION_DOCS:
            available = list(JURISDICTION_DOCS.keys())
            return {"error": f"Unknown jurisdiction '{code}'. Available: {available}"}

        name, doc_fn = JURISDICTION_DOCS[code]
        docs = doc_fn()
        self.checklists[code] = docs

        # Organize by category
        categories = {}
        for doc in docs:
            if doc.category not in categories:
                categories[doc.category] = []
            categories[doc.category].append({
                "id": doc.id,
                "name": doc.name,
                "description": doc.description,
                "mandatory": doc.mandatory,
                "apostille_required": doc.apostille_required,
                "notarization_required": doc.notarization_required,
                "max_age_months": doc.max_age_months,
                "lead_time_days": doc.typical_lead_time_days,
                "responsible": doc.responsible_party,
                "status": doc.status,
            })

        total = len(docs)
        mandatory = sum(1 for d in docs if d.mandatory)
        apostille = sum(1 for d in docs if d.apostille_required)
        max_lead = max(d.typical_lead_time_days for d in docs)

        return {
            "jurisdiction": name,
            "code": code,
            "generated": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_documents": total,
                "mandatory": mandatory,
                "optional": total - mandatory,
                "requiring_apostille": apostille,
                "max_lead_time_days": max_lead,
                "recommended_start_weeks_before": max_lead // 7 + 4,
            },
            "categories": categories,
            "timeline_recommendation": self._generate_timeline(docs),
        }

    def _generate_timeline(self, docs: list[DocumentItem]) -> list[dict]:
        """Generate recommended document preparation timeline."""
        phases = [
            {"week": 1, "phase": "Initiation",
             "tasks": ["Engage local legal counsel", "Open local bank account if required",
                        "Begin corporate document gathering"]},
            {"week": 2, "phase": "Personal Documents",
             "tasks": ["Submit police clearance requests (longest lead time)",
                        "Begin personal declarations", "Collect passport copies"]},
            {"week": 4, "phase": "Technical Preparation",
             "tasks": ["Commission penetration test", "Begin system architecture documentation",
                        "Submit games for RNG testing"]},
            {"week": 6, "phase": "Financial Documents",
             "tasks": ["Obtain bank reference letters", "Finalize business plan",
                        "Prepare proof of capital"]},
            {"week": 8, "phase": "Compliance Documentation",
             "tasks": ["Finalize AML/CFT policies", "Draft terms and conditions",
                        "Prepare responsible gaming procedures"]},
            {"week": 10, "phase": "Review and Assembly",
             "tasks": ["Legal counsel review all documents", "Obtain apostilles and notarizations",
                        "Assemble application package"]},
            {"week": 12, "phase": "Submission",
             "tasks": ["Final review", "Submit application", "Pay application fees"]},
        ]
        return phases

    def export_csv(self, jurisdiction_code: str, output_path: str):
        """Export checklist to CSV for project tracking."""
        code = jurisdiction_code.upper()
        if code not in self.checklists:
            self.generate_checklist(code)

        docs = self.checklists.get(code, [])
        if not docs:
            logger.warning("No documents for %s", code)
            return

        path = Path(output_path)
        with open(path, "w", newline="") as f:
            fields = ["id", "name", "category", "mandatory", "apostille_required",
                       "notarization_required", "max_age_months", "typical_lead_time_days",
                       "responsible_party", "status", "notes"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for doc in docs:
                writer.writerow({k: getattr(doc, k) for k in fields})
        logger.info("Exported %d items to %s", len(docs), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="License Application Document Checklist Generator")
    parser.add_argument("--jurisdiction", type=str, help="Jurisdiction code (MGA, UKGC, CUR)")
    parser.add_argument("--all", action="store_true", help="Generate checklists for all jurisdictions")
    parser.add_argument("--export", type=str, help="Export to CSV file path")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    generator = LicenseDocGenerator()

    if args.all:
        for code in JURISDICTION_DOCS:
            result = generator.generate_checklist(code)
            print(json.dumps(result, indent=2))
            print()
        return

    if args.jurisdiction:
        result = generator.generate_checklist(args.jurisdiction)

        if args.export:
            generator.export_csv(args.jurisdiction, args.export)

        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(result["error"])
                return
            s = result["summary"]
            print(f"\n{'='*70}")
            print(f"  Document Checklist: {result['jurisdiction']}")
            print(f"{'='*70}\n")
            print(f"  Total documents:     {s['total_documents']}")
            print(f"  Mandatory:           {s['mandatory']}")
            print(f"  Requiring apostille: {s['requiring_apostille']}")
            print(f"  Max lead time:       {s['max_lead_time_days']} days")
            print(f"  Start preparation:   {s['recommended_start_weeks_before']} weeks before submission\n")

            for cat, items in result["categories"].items():
                print(f"  [{cat.upper()}] ({len(items)} documents)")
                for item in items:
                    flag = " [APOSTILLE]" if item["apostille_required"] else ""
                    age = f" [<{item['max_age_months']}mo]" if item["max_age_months"] else ""
                    print(f"    {item['id']:12s} {item['name']}{flag}{age}")
                    print(f"               {item['description']}")
                print()
    else:
        print("Available jurisdictions:")
        for code, (name, _) in JURISDICTION_DOCS.items():
            print(f"  {code:6s} — {name}")
        print("\nUsage: python license_doc_generator.py --jurisdiction MGA")


if __name__ == "__main__":
    main()
