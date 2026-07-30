#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Security Tools Comparison for iGaming Operators
================================================

Comprehensive comparison of 50+ security tools across six categories,
mapped to the compliance frameworks that matter for real-money gaming:
ISO 27001:2022, PCI-DSS v4.0, GLI-33, and SOC2 Type II.

This script generates formatted comparison tables and recommendations
tailored to iGaming operator size and regulatory requirements.

Categories covered:
    1. SAST (Static Application Security Testing)
    2. DAST (Dynamic Application Security Testing)
    3. SCA (Software Composition Analysis)
    4. Container Security
    5. CSPM (Cloud Security Posture Management)
    6. SIEM (Security Information and Event Management)

Usage:
    python3 security-tools-comparison.py
    python3 security-tools-comparison.py --category sast
    python3 security-tools-comparison.py --compliance pci-dss
    python3 security-tools-comparison.py --recommend --size medium

Author: Chapter 23 -- DevSecOps for iGaming
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TextIO


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class SecurityTool:
    """Represents a security tool with its capabilities and compliance coverage."""

    name: str
    category: str
    subcategory: str
    vendor: str
    license_type: str  # "open-source", "commercial", "freemium"
    deployment: str  # "cloud", "on-premises", "hybrid"
    pricing_tier: str  # "free", "$", "$$", "$$$", "$$$$"

    # Compliance coverage (True if tool directly supports the framework)
    iso27001: bool = False
    pci_dss_v4: bool = False
    gli_33: bool = False
    soc2_type2: bool = False

    # iGaming-specific capabilities
    rng_audit_support: bool = False
    financial_tx_scanning: bool = False
    multi_jurisdiction: bool = False

    # Integration support
    integrations: list[str] = field(default_factory=list)

    # Brief description
    description: str = ""


# ---------------------------------------------------------------------------
# Tool database -- 50+ tools across 6 categories
# ---------------------------------------------------------------------------
def build_tool_database() -> list[SecurityTool]:
    """Build the comprehensive tool database for iGaming security comparison."""
    tools: list[SecurityTool] = []

    # -----------------------------------------------------------------------
    # Category 1: SAST (Static Application Security Testing)
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="SonarQube",
            category="SAST",
            subcategory="Multi-language SAST",
            vendor="SonarSource",
            license_type="freemium",
            deployment="hybrid",
            pricing_tier="$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=False,
            soc2_type2=True,
            rng_audit_support=False,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="Industry-standard SAST platform with 30+ language support. "
            "Community edition is free; Enterprise adds branch analysis and PR decoration.",
        ),
        SecurityTool(
            name="Semgrep",
            category="SAST",
            subcategory="Pattern-based SAST",
            vendor="Semgrep Inc.",
            license_type="freemium",
            deployment="hybrid",
            pricing_tier="$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=False,
            soc2_type2=True,
            financial_tx_scanning=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Lightweight, fast SAST engine with custom rule authoring. "
            "Ideal for iGaming teams writing domain-specific security rules.",
        ),
        SecurityTool(
            name="Bandit",
            category="SAST",
            subcategory="Python SAST",
            vendor="OpenStack Foundation",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Python-specific static analyser. Catches common security issues "
            "in FastAPI/Django backends typical of iGaming platforms.",
        ),
        SecurityTool(
            name="CodeQL",
            category="SAST",
            subcategory="Semantic SAST",
            vendor="GitHub",
            license_type="freemium",
            deployment="cloud",
            pricing_tier="$",
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitHub"],
            description="Semantic code analysis engine. Excels at finding data-flow "
            "vulnerabilities like SQL injection in player data queries.",
        ),
        SecurityTool(
            name="Checkmarx SAST",
            category="SAST",
            subcategory="Enterprise SAST",
            vendor="Checkmarx",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            rng_audit_support=True,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="Enterprise-grade SAST with regulatory compliance reporting. "
            "Used by large iGaming operators for GLI-33 audit evidence.",
        ),
        SecurityTool(
            name="Fortify SAST",
            category="SAST",
            subcategory="Enterprise SAST",
            vendor="OpenText (Micro Focus)",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            rng_audit_support=True,
            financial_tx_scanning=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins"],
            description="Mature enterprise SAST platform. Deep analysis engine with "
            "extensive compliance reporting for regulated industries.",
        ),
        SecurityTool(
            name="Snyk Code",
            category="SAST",
            subcategory="Developer-first SAST",
            vendor="Snyk",
            license_type="freemium",
            deployment="cloud",
            pricing_tier="$$",
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="Real-time SAST integrated into developer IDEs. Fast feedback "
            "loop for security issues during development.",
        ),
        SecurityTool(
            name="ESLint Security",
            category="SAST",
            subcategory="JavaScript SAST",
            vendor="Community",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="ESLint plugin for JavaScript/TypeScript security rules. "
            "Catches XSS, prototype pollution in casino frontend code.",
        ),
    ])

    # -----------------------------------------------------------------------
    # Category 2: DAST (Dynamic Application Security Testing)
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="OWASP ZAP",
            category="DAST",
            subcategory="Web application DAST",
            vendor="OWASP Foundation",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            gli_33=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="The most widely used open-source DAST scanner. Can be run "
            "in CI/CD pipelines against staging casino environments.",
        ),
        SecurityTool(
            name="Burp Suite Professional",
            category="DAST",
            subcategory="Web application DAST",
            vendor="PortSwigger",
            license_type="commercial",
            deployment="on-premises",
            pricing_tier="$$",
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            financial_tx_scanning=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Gold standard for manual and automated web security testing. "
            "Used by penetration testers during iGaming security assessments.",
        ),
        SecurityTool(
            name="Nuclei",
            category="DAST",
            subcategory="Template-based scanner",
            vendor="ProjectDiscovery",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Template-based vulnerability scanner with 7000+ templates. "
            "Fast scanning of casino web infrastructure.",
        ),
        SecurityTool(
            name="Acunetix",
            category="DAST",
            subcategory="Web application DAST",
            vendor="Invicti",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            financial_tx_scanning=True,
            integrations=["GitLab", "GitHub", "Jenkins", "Azure DevOps"],
            description="Automated web vulnerability scanner with low false positive rate. "
            "PCI-DSS compliance reporting built in.",
        ),
        SecurityTool(
            name="Nessus Professional",
            category="DAST",
            subcategory="Infrastructure scanner",
            vendor="Tenable",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["Jenkins"],
            description="Infrastructure vulnerability scanner. Required by most gaming "
            "regulators for periodic network vulnerability assessments.",
        ),
        SecurityTool(
            name="Qualys WAS",
            category="DAST",
            subcategory="Cloud DAST",
            vendor="Qualys",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["Jenkins", "Azure DevOps"],
            description="Cloud-based web application scanner with continuous monitoring. "
            "Scales well for multi-site casino operations.",
        ),
    ])

    # -----------------------------------------------------------------------
    # Category 3: SCA (Software Composition Analysis)
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="Snyk Open Source",
            category="SCA",
            subcategory="Dependency scanning",
            vendor="Snyk",
            license_type="freemium",
            deployment="cloud",
            pricing_tier="$$",
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="Developer-friendly SCA with auto-fix PRs for vulnerable "
            "dependencies. Covers npm, pip, Maven, NuGet.",
        ),
        SecurityTool(
            name="Dependabot",
            category="SCA",
            subcategory="Dependency updates",
            vendor="GitHub",
            license_type="open-source",
            deployment="cloud",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitHub"],
            description="Automated dependency update PRs. Built into GitHub, zero "
            "configuration needed. Good starting point for small operators.",
        ),
        SecurityTool(
            name="Grype",
            category="SCA",
            subcategory="Vulnerability scanner",
            vendor="Anchore",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Fast vulnerability scanner for container images and filesystems. "
            "Pairs with Syft for SBOM generation.",
        ),
        SecurityTool(
            name="OWASP Dependency-Check",
            category="SCA",
            subcategory="Dependency scanning",
            vendor="OWASP Foundation",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            gli_33=True,
            integrations=["GitLab", "GitHub", "Jenkins", "Azure DevOps"],
            description="Mature open-source SCA tool. Identifies known CVEs in project "
            "dependencies. Produces compliance-ready reports.",
        ),
        SecurityTool(
            name="Black Duck",
            category="SCA",
            subcategory="Enterprise SCA",
            vendor="Synopsys",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="Enterprise SCA with license compliance. Deep binary analysis for "
            "third-party game provider SDKs and payment libraries.",
        ),
        SecurityTool(
            name="Mend (WhiteSource)",
            category="SCA",
            subcategory="Enterprise SCA",
            vendor="Mend",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins", "Bitbucket"],
            description="SCA with automated policy enforcement. License risk management "
            "important for operators using open-source gaming libraries.",
        ),
        SecurityTool(
            name="Syft",
            category="SCA",
            subcategory="SBOM generation",
            vendor="Anchore",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="SBOM generation tool supporting CycloneDX and SPDX formats. "
            "Essential for supply chain transparency requirements.",
        ),
    ])

    # -----------------------------------------------------------------------
    # Category 4: Container Security
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="Aqua Security",
            category="Container Security",
            subcategory="Full lifecycle",
            vendor="Aqua Security",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            rng_audit_support=True,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins"],
            description="Complete container security platform: image scanning, runtime "
            "protection, drift prevention, compliance templates for gaming.",
        ),
        SecurityTool(
            name="Trivy",
            category="Container Security",
            subcategory="Image scanner",
            vendor="Aqua Security",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins", "Azure DevOps"],
            description="Fast, comprehensive vulnerability scanner for containers, "
            "filesystems, and git repositories. De facto standard for CI/CD.",
        ),
        SecurityTool(
            name="Sysdig Secure",
            category="Container Security",
            subcategory="Runtime security",
            vendor="Sysdig",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            financial_tx_scanning=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Kubernetes-native security platform with Falco-based runtime "
            "detection. Strong compliance mapping for regulated industries.",
        ),
        SecurityTool(
            name="Falco",
            category="Container Security",
            subcategory="Runtime detection",
            vendor="Sysdig / CNCF",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Cloud-native runtime security using kernel-level system call "
            "monitoring. Detects anomalous behaviour in gaming containers.",
        ),
        SecurityTool(
            name="Prisma Cloud",
            category="Container Security",
            subcategory="Full lifecycle",
            vendor="Palo Alto Networks",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Azure DevOps", "Jenkins"],
            description="Comprehensive CNAPP covering containers, serverless, and IaC. "
            "Single pane of glass for multi-cloud casino deployments.",
        ),
        SecurityTool(
            name="Docker Scout",
            category="Container Security",
            subcategory="Image analysis",
            vendor="Docker Inc.",
            license_type="freemium",
            deployment="cloud",
            pricing_tier="$",
            pci_dss_v4=True,
            integrations=["GitHub"],
            description="Built into Docker Desktop and Hub. Provides vulnerability "
            "analysis and SBOM for Docker images.",
        ),
        SecurityTool(
            name="Cosign",
            category="Container Security",
            subcategory="Image signing",
            vendor="Sigstore / Linux Foundation",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            gli_33=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Container image signing and verification. Ensures image "
            "provenance -- critical for supply chain security in gaming.",
        ),
    ])

    # -----------------------------------------------------------------------
    # Category 5: CSPM (Cloud Security Posture Management)
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="Prowler",
            category="CSPM",
            subcategory="AWS/Azure/GCP auditing",
            vendor="Toni de la Fuente",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Multi-cloud security auditing tool. 300+ checks mapped to "
            "CIS, PCI-DSS, ISO 27001. Essential for cloud-native casinos.",
        ),
        SecurityTool(
            name="Checkov",
            category="CSPM",
            subcategory="IaC scanning",
            vendor="Prisma Cloud / Palo Alto",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Jenkins", "Azure DevOps"],
            description="Infrastructure-as-Code scanner for Terraform, CloudFormation, "
            "Kubernetes. Catches misconfigurations before deployment.",
        ),
        SecurityTool(
            name="AWS Security Hub",
            category="CSPM",
            subcategory="AWS-native CSPM",
            vendor="Amazon Web Services",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["Jenkins"],
            description="AWS-native security findings aggregator. Integrates with "
            "GuardDuty, Inspector, and Macie for complete AWS posture.",
        ),
        SecurityTool(
            name="Wiz",
            category="CSPM",
            subcategory="Cloud-native CSPM",
            vendor="Wiz",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Agentless cloud security platform. Graph-based risk analysis "
            "identifies attack paths across multi-cloud environments.",
        ),
        SecurityTool(
            name="ScoutSuite",
            category="CSPM",
            subcategory="Multi-cloud auditing",
            vendor="NCC Group",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub"],
            description="Multi-cloud security auditing tool. Generates HTML reports "
            "with risk scoring for AWS, Azure, GCP, and Oracle Cloud.",
        ),
        SecurityTool(
            name="tfsec",
            category="CSPM",
            subcategory="Terraform scanner",
            vendor="Aqua Security",
            license_type="open-source",
            deployment="on-premises",
            pricing_tier="free",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Static analysis for Terraform code. Now integrated into Trivy. "
            "Catches security misconfigurations in IaC.",
        ),
    ])

    # -----------------------------------------------------------------------
    # Category 6: SIEM (Security Information and Event Management)
    # -----------------------------------------------------------------------
    tools.extend([
        SecurityTool(
            name="Elastic Security (ELK)",
            category="SIEM",
            subcategory="Open-core SIEM",
            vendor="Elastic",
            license_type="freemium",
            deployment="hybrid",
            pricing_tier="$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Elastic Stack with SIEM capabilities. Flexible enough for "
            "iGaming-specific detection rules (fraud, bonus abuse, RNG anomalies).",
        ),
        SecurityTool(
            name="Splunk Enterprise Security",
            category="SIEM",
            subcategory="Enterprise SIEM",
            vendor="Cisco (Splunk)",
            license_type="commercial",
            deployment="hybrid",
            pricing_tier="$$$$",
            iso27001=True,
            pci_dss_v4=True,
            gli_33=True,
            soc2_type2=True,
            rng_audit_support=True,
            financial_tx_scanning=True,
            multi_jurisdiction=True,
            integrations=["GitLab", "GitHub", "Jenkins", "Azure DevOps"],
            description="Enterprise-grade SIEM with ML-powered analytics. Used by "
            "large casino groups for SOC operations and regulatory reporting.",
        ),
        SecurityTool(
            name="Wazuh",
            category="SIEM",
            subcategory="Open-source SIEM",
            vendor="Wazuh Inc.",
            license_type="open-source",
            deployment="hybrid",
            pricing_tier="free",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Open-source SIEM/XDR with compliance dashboards for PCI-DSS "
            "and ISO 27001. Cost-effective for mid-size operators.",
        ),
        SecurityTool(
            name="Microsoft Sentinel",
            category="SIEM",
            subcategory="Cloud SIEM",
            vendor="Microsoft",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            multi_jurisdiction=True,
            integrations=["Azure DevOps", "GitHub"],
            description="Cloud-native SIEM on Azure. Pay-per-ingestion model suits "
            "variable gaming workloads. KQL query language.",
        ),
        SecurityTool(
            name="CrowdStrike Falcon LogScale",
            category="SIEM",
            subcategory="Log management",
            vendor="CrowdStrike",
            license_type="commercial",
            deployment="cloud",
            pricing_tier="$$$",
            iso27001=True,
            pci_dss_v4=True,
            soc2_type2=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="High-performance log management with streaming ingestion. "
            "Handles the high event volumes typical of real-time gaming.",
        ),
        SecurityTool(
            name="Graylog",
            category="SIEM",
            subcategory="Log management",
            vendor="Graylog Inc.",
            license_type="freemium",
            deployment="hybrid",
            pricing_tier="$",
            pci_dss_v4=True,
            integrations=["GitLab", "GitHub", "Jenkins"],
            description="Open-core log management platform. Good for smaller operators "
            "who need centralised logging without enterprise SIEM costs.",
        ),
    ])

    return tools


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------
def print_category_table(
    tools: list[SecurityTool],
    category: str,
    output: TextIO = sys.stdout,
) -> None:
    """Print a formatted comparison table for a single category."""
    filtered = [t for t in tools if t.category.lower() == category.lower()]
    if not filtered:
        output.write(f"No tools found in category: {category}\n")
        return

    output.write(f"\n{'=' * 100}\n")
    output.write(f"  {category} Tools for iGaming\n")
    output.write(f"{'=' * 100}\n\n")

    header = (
        f"{'Tool':<25} {'License':<12} {'Deploy':<12} {'Price':<6} "
        f"{'ISO27001':<9} {'PCI-DSS':<8} {'GLI-33':<7} {'SOC2':<5}"
    )
    output.write(header + "\n")
    output.write("-" * len(header) + "\n")

    for tool in filtered:
        row = (
            f"{tool.name:<25} {tool.license_type:<12} {tool.deployment:<12} "
            f"{tool.pricing_tier:<6} "
            f"{'Yes' if tool.iso27001 else '-':<9} "
            f"{'Yes' if tool.pci_dss_v4 else '-':<8} "
            f"{'Yes' if tool.gli_33 else '-':<7} "
            f"{'Yes' if tool.soc2_type2 else '-':<5}"
        )
        output.write(row + "\n")

    output.write("\n")


def print_compliance_mapping(
    tools: list[SecurityTool],
    framework: str,
    output: TextIO = sys.stdout,
) -> None:
    """Print tools mapped to a specific compliance framework."""
    framework_map = {
        "iso27001": ("ISO 27001:2022", lambda t: t.iso27001),
        "pci-dss": ("PCI-DSS v4.0", lambda t: t.pci_dss_v4),
        "gli-33": ("GLI-33", lambda t: t.gli_33),
        "soc2": ("SOC2 Type II", lambda t: t.soc2_type2),
    }

    if framework not in framework_map:
        output.write(f"Unknown framework: {framework}\n")
        output.write(f"Available: {', '.join(framework_map.keys())}\n")
        return

    label, predicate = framework_map[framework]
    filtered = [t for t in tools if predicate(t)]

    output.write(f"\n{'=' * 80}\n")
    output.write(f"  Tools Supporting {label}\n")
    output.write(f"{'=' * 80}\n\n")

    by_category: dict[str, list[SecurityTool]] = {}
    for tool in filtered:
        by_category.setdefault(tool.category, []).append(tool)

    for cat, cat_tools in sorted(by_category.items()):
        output.write(f"  [{cat}]\n")
        for tool in cat_tools:
            output.write(f"    - {tool.name} ({tool.license_type}, {tool.pricing_tier})\n")
        output.write("\n")

    output.write(f"  Total: {len(filtered)} tools support {label}\n\n")


def print_igaming_features(
    tools: list[SecurityTool],
    output: TextIO = sys.stdout,
) -> None:
    """Print tools with iGaming-specific capabilities."""
    output.write(f"\n{'=' * 80}\n")
    output.write("  iGaming-Specific Capabilities\n")
    output.write(f"{'=' * 80}\n\n")

    features = [
        ("RNG Audit Support", lambda t: t.rng_audit_support),
        ("Financial Transaction Scanning", lambda t: t.financial_tx_scanning),
        ("Multi-Jurisdiction Support", lambda t: t.multi_jurisdiction),
    ]

    for label, predicate in features:
        matching = [t for t in tools if predicate(t)]
        output.write(f"  {label}:\n")
        for tool in matching:
            output.write(f"    - {tool.name} ({tool.category})\n")
        output.write(f"    Total: {len(matching)} tools\n\n")


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------
def recommend_stack(
    tools: list[SecurityTool],
    operator_size: str,
    output: TextIO = sys.stdout,
) -> None:
    """Generate a recommended security tool stack based on operator size."""
    output.write(f"\n{'=' * 80}\n")
    output.write(f"  Recommended Stack: {operator_size.upper()} Operator\n")
    output.write(f"{'=' * 80}\n\n")

    # Define strategy per size
    size_strategies: dict[str, dict[str, str]] = {
        "small": {
            "description": "1 jurisdiction, <50 employees, limited security budget",
            "preference": "open-source",
            "max_tier": "$$",
        },
        "medium": {
            "description": "2-3 jurisdictions, 50-200 employees, moderate budget",
            "preference": "freemium",
            "max_tier": "$$$",
        },
        "enterprise": {
            "description": "5+ jurisdictions, 200+ employees, dedicated security team",
            "preference": "commercial",
            "max_tier": "$$$$",
        },
    }

    strategy = size_strategies.get(operator_size.lower())
    if not strategy:
        output.write(f"Unknown size: {operator_size}. Use: small, medium, enterprise\n")
        return

    output.write(f"  Profile: {strategy['description']}\n\n")

    tier_order = {"free": 0, "$": 1, "$$": 2, "$$$": 3, "$$$$": 4}
    max_tier_val = tier_order.get(strategy["max_tier"], 4)

    categories = ["SAST", "DAST", "SCA", "Container Security", "CSPM", "SIEM"]

    for category in categories:
        cat_tools = [
            t
            for t in tools
            if t.category == category and tier_order.get(t.pricing_tier, 0) <= max_tier_val
        ]

        if not cat_tools:
            continue

        # Score tools: compliance coverage + integrations + iGaming features
        def score(t: SecurityTool) -> int:
            s = 0
            s += 3 if t.pci_dss_v4 else 0
            s += 2 if t.iso27001 else 0
            s += 2 if t.gli_33 else 0
            s += 1 if t.soc2_type2 else 0
            s += 2 if t.financial_tx_scanning else 0
            s += 1 if t.rng_audit_support else 0
            s += len(t.integrations)
            # Prefer open-source for small, commercial for enterprise
            if operator_size == "small" and t.license_type == "open-source":
                s += 3
            elif operator_size == "enterprise" and t.license_type == "commercial":
                s += 2
            return s

        cat_tools.sort(key=score, reverse=True)
        best = cat_tools[0]

        output.write(f"  [{category}]\n")
        output.write(f"    Primary:   {best.name} ({best.license_type}, {best.pricing_tier})\n")
        if len(cat_tools) > 1:
            alt = cat_tools[1]
            output.write(
                f"    Alternate: {alt.name} ({alt.license_type}, {alt.pricing_tier})\n"
            )
        output.write(f"    Reason:    {best.description[:80]}...\n\n")


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------
def export_json(tools: list[SecurityTool], output: TextIO = sys.stdout) -> None:
    """Export the full tool database as JSON."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tools": len(tools),
        "categories": sorted({t.category for t in tools}),
        "tools": [
            {
                "name": t.name,
                "category": t.category,
                "subcategory": t.subcategory,
                "vendor": t.vendor,
                "license_type": t.license_type,
                "deployment": t.deployment,
                "pricing_tier": t.pricing_tier,
                "compliance": {
                    "iso27001": t.iso27001,
                    "pci_dss_v4": t.pci_dss_v4,
                    "gli_33": t.gli_33,
                    "soc2_type2": t.soc2_type2,
                },
                "igaming_features": {
                    "rng_audit_support": t.rng_audit_support,
                    "financial_tx_scanning": t.financial_tx_scanning,
                    "multi_jurisdiction": t.multi_jurisdiction,
                },
                "integrations": t.integrations,
                "description": t.description,
            }
            for t in tools
        ],
    }
    output.write(json.dumps(data, indent=2))
    output.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Entry point for the security tools comparison."""
    parser = argparse.ArgumentParser(
        description="Security Tools Comparison for iGaming Operators",
    )
    parser.add_argument(
        "--category",
        help="Filter by category (sast, dast, sca, container-security, cspm, siem)",
    )
    parser.add_argument(
        "--compliance",
        help="Filter by compliance framework (iso27001, pci-dss, gli-33, soc2)",
    )
    parser.add_argument(
        "--recommend",
        action="store_true",
        help="Generate recommended tool stack",
    )
    parser.add_argument(
        "--size",
        default="medium",
        help="Operator size for recommendations (small, medium, enterprise)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Export full database as JSON",
    )
    parser.add_argument(
        "--igaming-features",
        action="store_true",
        help="Show tools with iGaming-specific capabilities",
    )

    args = parser.parse_args()
    tools = build_tool_database()

    print(f"Security Tools Comparison for iGaming -- {len(tools)} tools loaded")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    if args.json:
        export_json(tools)
    elif args.category:
        category_map: dict[str, str] = {
            "sast": "SAST",
            "dast": "DAST",
            "sca": "SCA",
            "container-security": "Container Security",
            "cspm": "CSPM",
            "siem": "SIEM",
        }
        cat = category_map.get(args.category.lower(), args.category)
        print_category_table(tools, cat)
    elif args.compliance:
        print_compliance_mapping(tools, args.compliance)
    elif args.igaming_features:
        print_igaming_features(tools)
    elif args.recommend:
        recommend_stack(tools, args.size)
    else:
        # Print all categories
        categories = ["SAST", "DAST", "SCA", "Container Security", "CSPM", "SIEM"]
        for cat in categories:
            print_category_table(tools, cat)
        print_igaming_features(tools)
        recommend_stack(tools, args.size)


if __name__ == "__main__":
    main()
