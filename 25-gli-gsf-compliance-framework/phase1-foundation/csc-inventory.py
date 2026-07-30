#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
csc-inventory.py - Critical System Component Inventory Tool

GLI-GSF-1, Section 1.3 requires a complete inventory of all Critical System
Components (CSCs) within the Gaming Production Environment (GPE). This script
scans infrastructure to discover and catalog CSCs across cloud providers,
on-premises systems, and third-party integrations.

CSC categories per GLI-GSF-1:
  - RNG systems (always classified Critical)
  - Game logic / game engine servers
  - Payment gateways and processing systems
  - Player account and identity databases
  - Bonus and promotional engines
  - Back-office administration systems
  - Real-time communication (WebSocket/RTC) servers
  - CDN and content delivery infrastructure
  - AML / transaction monitoring systems
  - Mobile application backends

Usage:
    python3 csc-inventory.py --provider aws --region eu-west-1
    python3 csc-inventory.py --provider aws --region eu-west-1 --output json
    python3 csc-inventory.py --config config.yaml
    python3 csc-inventory.py --manual  # manual entry mode

Requirements:
    pip install boto3 pyyaml  (for AWS scanning)
    pip install google-cloud-compute  (for GCP scanning)

Output:
    CSC inventory report in JSON, CSV, or Markdown format
"""

import argparse
import csv
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION = "1.0.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("csc-inventory")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class RiskLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class CSCCategory(str, Enum):
    RNG = "RNG System"
    GAME_SERVER = "Game Logic Server"
    PAYMENT = "Payment Gateway"
    PLAYER_DB = "Player Database"
    BONUS_ENGINE = "Bonus Engine"
    SPORTSBOOK = "Sportsbook/Odds Engine"
    BACKOFFICE = "Back-Office System"
    CRM = "CRM System"
    AML = "AML/Transaction Monitoring"
    CDN = "CDN Infrastructure"
    MOBILE_BACKEND = "Mobile Backend"
    RTC = "RTC/WebSocket Server"
    SIEM = "SIEM/Monitoring"
    IAM = "Identity & Access Management"
    OTHER = "Other"


# GLI-GSF mandates that RNG is always Critical. These are the default
# risk mappings; operators should adjust based on their own risk assessment.
DEFAULT_RISK_MAP = {
    CSCCategory.RNG: RiskLevel.CRITICAL,
    CSCCategory.GAME_SERVER: RiskLevel.CRITICAL,
    CSCCategory.PAYMENT: RiskLevel.CRITICAL,
    CSCCategory.SPORTSBOOK: RiskLevel.CRITICAL,
    CSCCategory.PLAYER_DB: RiskLevel.HIGH,
    CSCCategory.BONUS_ENGINE: RiskLevel.HIGH,
    CSCCategory.BACKOFFICE: RiskLevel.HIGH,
    CSCCategory.AML: RiskLevel.HIGH,
    CSCCategory.MOBILE_BACKEND: RiskLevel.HIGH,
    CSCCategory.RTC: RiskLevel.HIGH,
    CSCCategory.IAM: RiskLevel.HIGH,
    CSCCategory.SIEM: RiskLevel.HIGH,
    CSCCategory.CRM: RiskLevel.MEDIUM,
    CSCCategory.CDN: RiskLevel.MEDIUM,
    CSCCategory.OTHER: RiskLevel.MEDIUM,
}

# OGIS domain mapping for each CSC category
OGIS_DOMAIN_MAP = {
    CSCCategory.RNG: ["OGIS-1"],
    CSCCategory.GAME_SERVER: ["OGIS-1", "OGIS-3"],
    CSCCategory.PAYMENT: ["OGIS-2"],
    CSCCategory.PLAYER_DB: ["OGIS-2", "OGIS-3"],
    CSCCategory.BONUS_ENGINE: ["OGIS-3"],
    CSCCategory.SPORTSBOOK: ["OGIS-1", "OGIS-3"],
    CSCCategory.BACKOFFICE: ["OGIS-2"],
    CSCCategory.CRM: ["OGIS-2"],
    CSCCategory.AML: ["OGIS-3"],
    CSCCategory.CDN: ["OGIS-5"],
    CSCCategory.MOBILE_BACKEND: ["OGIS-4"],
    CSCCategory.RTC: ["OGIS-4", "OGIS-5"],
    CSCCategory.SIEM: ["OGIS-3"],
    CSCCategory.IAM: ["OGIS-2"],
    CSCCategory.OTHER: [],
}


@dataclass
class CSCEntry:
    """Represents a single Critical System Component."""

    csc_id: str
    name: str
    category: str
    description: str
    risk_level: str
    ogis_domains: List[str]
    hostname: str = ""
    ip_address: str = ""
    provider: str = ""  # aws, gcp, on-prem, third-party
    region: str = ""
    instance_type: str = ""
    os_family: str = ""
    owner: str = ""
    data_classification: str = ""
    encryption_at_rest: bool = False
    encryption_in_transit: bool = False
    backup_enabled: bool = False
    monitoring_enabled: bool = False
    last_patched: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    notes: str = ""
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class CSCInventory:
    """Complete CSC inventory for the GPE."""

    organization: str
    gpe_name: str
    inventory_date: str
    version: str
    gig_level: int
    entries: List[CSCEntry] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.entries)

    @property
    def critical_count(self) -> int:
        return sum(
            1 for e in self.entries if e.risk_level == RiskLevel.CRITICAL.value
        )

    @property
    def high_count(self) -> int:
        return sum(
            1 for e in self.entries if e.risk_level == RiskLevel.HIGH.value
        )

    def by_category(self) -> Dict[str, List[CSCEntry]]:
        result: Dict[str, List[CSCEntry]] = {}
        for entry in self.entries:
            result.setdefault(entry.category, []).append(entry)
        return result

    def by_risk(self) -> Dict[str, List[CSCEntry]]:
        result: Dict[str, List[CSCEntry]] = {}
        for entry in self.entries:
            result.setdefault(entry.risk_level, []).append(entry)
        return result


# ---------------------------------------------------------------------------
# Discovery: AWS
# ---------------------------------------------------------------------------
class AWSDiscovery:
    """Discover CSCs in AWS infrastructure."""

    def __init__(self, region: str, profile: Optional[str] = None):
        self.region = region
        self.profile = profile
        self._ec2 = None
        self._rds = None

    def _get_boto3_session(self):
        try:
            import boto3  # ty:ignore[unresolved-import]
        except ImportError:
            logger.error("boto3 is required for AWS discovery. Install: pip install boto3")
            sys.exit(1)

        kwargs = {"region_name": self.region}
        if self.profile:
            kwargs["profile_name"] = self.profile
        return boto3.Session(**kwargs)

    def discover_ec2(self) -> List[CSCEntry]:
        """Discover EC2 instances and classify by tags."""
        entries = []
        session = self._get_boto3_session()
        ec2 = session.client("ec2")

        logger.info(f"Scanning EC2 instances in {self.region}...")

        try:
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate():
                for reservation in page["Reservations"]:
                    for instance in reservation["Instances"]:
                        if instance["State"]["Name"] != "running":
                            continue

                        tags = {
                            t["Key"]: t["Value"]
                            for t in instance.get("Tags", [])
                        }

                        category = self._classify_instance(tags, instance)
                        risk = DEFAULT_RISK_MAP.get(
                            category, RiskLevel.MEDIUM
                        )
                        ogis = OGIS_DOMAIN_MAP.get(category, [])

                        entry = CSCEntry(
                            csc_id=f"AWS-EC2-{instance['InstanceId']}",
                            name=tags.get("Name", instance["InstanceId"]),
                            category=category.value,
                            description=f"EC2 instance in {self.region}",
                            risk_level=risk.value,
                            ogis_domains=ogis,
                            hostname=instance.get("PrivateDnsName", ""),
                            ip_address=instance.get("PrivateIpAddress", ""),
                            provider="aws",
                            region=self.region,
                            instance_type=instance.get("InstanceType", ""),
                            os_family=instance.get("Platform", "linux"),
                            owner=tags.get("Owner", ""),
                            encryption_at_rest=self._check_ebs_encryption(
                                ec2, instance
                            ),
                            tags=tags,
                        )
                        entries.append(entry)

            logger.info(f"Found {len(entries)} running EC2 instances")
        except Exception as e:
            logger.error(f"EC2 discovery failed: {e}")

        return entries

    def discover_rds(self) -> List[CSCEntry]:
        """Discover RDS instances."""
        entries = []
        session = self._get_boto3_session()
        rds = session.client("rds")

        logger.info(f"Scanning RDS instances in {self.region}...")

        try:
            paginator = rds.get_paginator("describe_db_instances")
            for page in paginator.paginate():
                for db in page["DBInstances"]:
                    # Classify based on DB identifier naming conventions
                    category = self._classify_database(db)
                    risk = DEFAULT_RISK_MAP.get(category, RiskLevel.HIGH)
                    ogis = OGIS_DOMAIN_MAP.get(category, [])

                    entry = CSCEntry(
                        csc_id=f"AWS-RDS-{db['DBInstanceIdentifier']}",
                        name=db["DBInstanceIdentifier"],
                        category=category.value,
                        description=f"RDS {db['Engine']} {db['EngineVersion']}",
                        risk_level=risk.value,
                        ogis_domains=ogis,
                        hostname=db.get("Endpoint", {}).get("Address", ""),
                        provider="aws",
                        region=self.region,
                        instance_type=db.get("DBInstanceClass", ""),
                        encryption_at_rest=db.get("StorageEncrypted", False),
                        encryption_in_transit=db.get(
                            "CACertificateIdentifier", ""
                        )
                        != "",
                        backup_enabled=db.get("BackupRetentionPeriod", 0) > 0,
                        monitoring_enabled=db.get(
                            "MonitoringInterval", 0
                        )
                        > 0,
                    )
                    entries.append(entry)

            logger.info(f"Found {len(entries)} RDS instances")
        except Exception as e:
            logger.error(f"RDS discovery failed: {e}")

        return entries

    def discover_all(self) -> List[CSCEntry]:
        """Run all AWS discovery methods."""
        entries = []
        entries.extend(self.discover_ec2())
        entries.extend(self.discover_rds())
        return entries

    @staticmethod
    def _classify_instance(
        tags: Dict[str, str], instance: dict
    ) -> CSCCategory:
        """Classify EC2 instance based on tags and naming conventions."""
        name = tags.get("Name", "").lower()
        service = tags.get("Service", "").lower()
        component = tags.get("Component", "").lower()

        combined = f"{name} {service} {component}"

        # Pattern matching for iGaming components
        if any(kw in combined for kw in ["rng", "random", "prng"]):
            return CSCCategory.RNG
        if any(kw in combined for kw in ["game", "slot", "table", "live-dealer"]):
            return CSCCategory.GAME_SERVER
        if any(kw in combined for kw in ["payment", "psp", "cashier", "withdraw"]):
            return CSCCategory.PAYMENT
        if any(kw in combined for kw in ["player", "account", "identity", "kyc"]):
            return CSCCategory.PLAYER_DB
        if any(kw in combined for kw in ["bonus", "promotion", "campaign"]):
            return CSCCategory.BONUS_ENGINE
        if any(kw in combined for kw in ["sport", "odds", "betting", "feed"]):
            return CSCCategory.SPORTSBOOK
        if any(kw in combined for kw in ["backoffice", "admin", "cms", "bo-"]):
            return CSCCategory.BACKOFFICE
        if any(kw in combined for kw in ["crm", "email", "notification"]):
            return CSCCategory.CRM
        if any(kw in combined for kw in ["aml", "fraud", "compliance", "monitor"]):
            return CSCCategory.AML
        if any(kw in combined for kw in ["cdn", "edge", "cache", "static"]):
            return CSCCategory.CDN
        if any(kw in combined for kw in ["mobile", "app-api", "ios", "android"]):
            return CSCCategory.MOBILE_BACKEND
        if any(kw in combined for kw in ["rtc", "websocket", "ws-", "realtime"]):
            return CSCCategory.RTC
        if any(kw in combined for kw in ["siem", "elk", "wazuh", "splunk"]):
            return CSCCategory.SIEM
        if any(kw in combined for kw in ["iam", "auth", "sso", "keycloak", "okta"]):
            return CSCCategory.IAM

        return CSCCategory.OTHER

    @staticmethod
    def _classify_database(db: dict) -> CSCCategory:
        """Classify RDS instance based on identifier."""
        db_id = db.get("DBInstanceIdentifier", "").lower()

        if any(kw in db_id for kw in ["player", "account", "user", "identity"]):
            return CSCCategory.PLAYER_DB
        if any(kw in db_id for kw in ["game", "slot", "casino"]):
            return CSCCategory.GAME_SERVER
        if any(kw in db_id for kw in ["payment", "transaction", "ledger"]):
            return CSCCategory.PAYMENT
        if any(kw in db_id for kw in ["bonus", "promo"]):
            return CSCCategory.BONUS_ENGINE
        if any(kw in db_id for kw in ["sport", "betting", "odds"]):
            return CSCCategory.SPORTSBOOK
        if any(kw in db_id for kw in ["aml", "fraud"]):
            return CSCCategory.AML
        if any(kw in db_id for kw in ["backoffice", "admin", "bo"]):
            return CSCCategory.BACKOFFICE

        return CSCCategory.PLAYER_DB  # Default: databases likely hold player data

    @staticmethod
    def _check_ebs_encryption(ec2_client, instance: dict) -> bool:
        """Check if all EBS volumes are encrypted."""
        try:
            for mapping in instance.get("BlockDeviceMappings", []):
                vol_id = mapping.get("Ebs", {}).get("VolumeId")
                if vol_id:
                    vol = ec2_client.describe_volumes(VolumeIds=[vol_id])
                    for v in vol["Volumes"]:
                        if not v.get("Encrypted", False):
                            return False
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Discovery: Manual Entry
# ---------------------------------------------------------------------------
class ManualDiscovery:
    """Interactive manual CSC entry for environments without API access."""

    CATEGORY_CHOICES = {str(i + 1): cat for i, cat in enumerate(CSCCategory)}

    def discover_all(self) -> List[CSCEntry]:
        """Interactively collect CSC entries."""
        entries = []
        csc_num = 1

        print("\n=== Manual CSC Inventory Entry ===")
        print("Enter CSC details. Type 'done' for name to finish.\n")

        while True:
            name = input(f"CSC #{csc_num} Name (or 'done'): ").strip()
            if name.lower() == "done":
                break

            print("\nCategories:")
            for key, cat in self.CATEGORY_CHOICES.items():
                risk = DEFAULT_RISK_MAP.get(cat, RiskLevel.MEDIUM)
                print(f"  {key}. {cat.value} [{risk.value}]")

            cat_choice = input("Category number: ").strip()
            category = self.CATEGORY_CHOICES.get(cat_choice, CSCCategory.OTHER)
            risk = DEFAULT_RISK_MAP.get(category, RiskLevel.MEDIUM)
            ogis = OGIS_DOMAIN_MAP.get(category, [])

            description = input("Description: ").strip()
            hostname = input("Hostname: ").strip()
            ip_address = input("IP Address: ").strip()
            provider = input("Provider (aws/gcp/on-prem/third-party): ").strip()
            owner = input("Owner (team/person): ").strip()

            entry = CSCEntry(
                csc_id=f"CSC-{csc_num:04d}",
                name=name,
                category=category.value,
                description=description,
                risk_level=risk.value,
                ogis_domains=ogis,
                hostname=hostname,
                ip_address=ip_address,
                provider=provider,
                owner=owner,
            )
            entries.append(entry)
            csc_num += 1
            print(f"  Added: {name} [{category.value}] - {risk.value}\n")

        return entries


# ---------------------------------------------------------------------------
# Discovery: Network Scan (lightweight, no nmap dependency)
# ---------------------------------------------------------------------------
class NetworkDiscovery:
    """Lightweight network discovery using standard library."""

    # Common ports for iGaming services
    GAMING_PORTS = {
        22: ("SSH", CSCCategory.OTHER),
        80: ("HTTP", CSCCategory.OTHER),
        443: ("HTTPS", CSCCategory.OTHER),
        3306: ("MySQL", CSCCategory.PLAYER_DB),
        5432: ("PostgreSQL", CSCCategory.PLAYER_DB),
        6379: ("Redis", CSCCategory.GAME_SERVER),
        8080: ("HTTP-Alt/Game-API", CSCCategory.GAME_SERVER),
        8443: ("HTTPS-Alt/Backoffice", CSCCategory.BACKOFFICE),
        9090: ("Prometheus", CSCCategory.SIEM),
        9200: ("Elasticsearch", CSCCategory.SIEM),
        27017: ("MongoDB", CSCCategory.PLAYER_DB),
        5672: ("RabbitMQ", CSCCategory.GAME_SERVER),
        9092: ("Kafka", CSCCategory.GAME_SERVER),
    }

    def __init__(self, subnet: str):
        self.subnet = subnet

    def scan_host(self, ip: str) -> List[CSCEntry]:
        """Scan a single host for known gaming service ports."""
        entries = []
        for port, (service, category) in self.GAMING_PORTS.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((ip, port))
                sock.close()

                if result == 0:
                    risk = DEFAULT_RISK_MAP.get(category, RiskLevel.MEDIUM)
                    ogis = OGIS_DOMAIN_MAP.get(category, [])

                    try:
                        hostname = socket.gethostbyaddr(ip)[0]
                    except socket.herror:
                        hostname = ip

                    entry = CSCEntry(
                        csc_id=f"NET-{ip.replace('.', '-')}-{port}",
                        name=f"{service} on {ip}:{port}",
                        category=category.value,
                        description=f"Discovered {service} service on port {port}",
                        risk_level=risk.value,
                        ogis_domains=ogis,
                        hostname=hostname,
                        ip_address=ip,
                        provider="on-prem",
                    )
                    entries.append(entry)
            except (socket.error, OSError):
                continue

        return entries


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
class ReportGenerator:
    """Generate CSC inventory reports in multiple formats."""

    @staticmethod
    def to_json(inventory: CSCInventory) -> str:
        """Export inventory as JSON (for ISF evidence packages)."""
        data = {
            "document_type": "CSC Inventory Register",
            "gsf_reference": "GLI-GSF-1, Section 1.3",
            "organization": inventory.organization,
            "gpe_name": inventory.gpe_name,
            "inventory_date": inventory.inventory_date,
            "version": inventory.version,
            "gig_level": inventory.gig_level,
            "summary": {
                "total_cscs": inventory.total_count,
                "critical_cscs": inventory.critical_count,
                "high_cscs": inventory.high_count,
                "categories": len(inventory.by_category()),
            },
            "entries": [asdict(e) for e in inventory.entries],
        }
        return json.dumps(data, indent=2, default=str)

    @staticmethod
    def to_csv(inventory: CSCInventory) -> str:
        """Export inventory as CSV."""
        output = io.StringIO()
        if not inventory.entries:
            return ""

        fieldnames = [
            "csc_id", "name", "category", "risk_level", "ogis_domains",
            "hostname", "ip_address", "provider", "region", "instance_type",
            "owner", "encryption_at_rest", "encryption_in_transit",
            "backup_enabled", "monitoring_enabled", "description",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for entry in inventory.entries:
            row = asdict(entry)
            row["ogis_domains"] = ", ".join(row["ogis_domains"])
            writer.writerow({k: row.get(k, "") for k in fieldnames})

        return output.getvalue()

    @staticmethod
    def to_markdown(inventory: CSCInventory) -> str:
        """Export inventory as Markdown report."""
        lines = [
            f"# CSC Inventory Register",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Organization** | {inventory.organization} |",
            f"| **GPE Name** | {inventory.gpe_name} |",
            f"| **Date** | {inventory.inventory_date} |",
            f"| **GIG Level** | GIG{inventory.gig_level} |",
            f"| **Total CSCs** | {inventory.total_count} |",
            f"| **Critical CSCs** | {inventory.critical_count} |",
            f"| **High-Risk CSCs** | {inventory.high_count} |",
            f"| **GLI-GSF Reference** | GLI-GSF-1, Section 1.3 |",
            f"",
            f"## CSC Register",
            f"",
            f"| ID | Name | Category | Risk | OGIS | Provider | Host |",
            f"|----|------|----------|------|------|----------|------|",
        ]

        for entry in inventory.entries:
            ogis = ", ".join(entry.ogis_domains)
            lines.append(
                f"| {entry.csc_id} | {entry.name} | {entry.category} "
                f"| **{entry.risk_level}** | {ogis} | {entry.provider} "
                f"| {entry.hostname or entry.ip_address} |"
            )

        # Summary by category
        lines.extend([
            f"",
            f"## Summary by Category",
            f"",
            f"| Category | Count | Risk Level |",
            f"|----------|-------|------------|",
        ])

        for cat_name, cat_entries in sorted(inventory.by_category().items()):
            risk = cat_entries[0].risk_level if cat_entries else "N/A"
            lines.append(f"| {cat_name} | {len(cat_entries)} | {risk} |")

        # Summary by risk
        lines.extend([
            f"",
            f"## Summary by Risk Level",
            f"",
            f"| Risk Level | Count | Percentage |",
            f"|-----------|-------|------------|",
        ])

        for risk_name, risk_entries in sorted(inventory.by_risk().items()):
            pct = (
                (len(risk_entries) / inventory.total_count * 100)
                if inventory.total_count > 0
                else 0
            )
            lines.append(
                f"| **{risk_name}** | {len(risk_entries)} | {pct:.1f}% |"
            )

        # Encryption compliance
        encrypted_rest = sum(1 for e in inventory.entries if e.encryption_at_rest)
        encrypted_transit = sum(
            1 for e in inventory.entries if e.encryption_in_transit
        )

        lines.extend([
            f"",
            f"## Encryption Status",
            f"",
            f"| Metric | Count | Coverage |",
            f"|--------|-------|----------|",
            f"| Encryption at Rest | {encrypted_rest}/{inventory.total_count} "
            f"| {encrypted_rest / max(inventory.total_count, 1) * 100:.0f}% |",
            f"| Encryption in Transit | {encrypted_transit}/{inventory.total_count} "
            f"| {encrypted_transit / max(inventory.total_count, 1) * 100:.0f}% |",
            f"",
            f"---",
            f"*Generated by csc-inventory.py v{VERSION}*",
        ])

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="CSC Inventory Tool for GLI-GSF-1, Section 1.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # AWS discovery
  python3 csc-inventory.py --provider aws --region eu-west-1

  # Manual entry mode
  python3 csc-inventory.py --manual --org "AcmetoCasino" --gpe "Production"

  # Network scan
  python3 csc-inventory.py --scan 10.0.0.0/24

  # Export formats
  python3 csc-inventory.py --manual --output json
  python3 csc-inventory.py --manual --output csv
  python3 csc-inventory.py --manual --output markdown
        """,
    )

    parser.add_argument("--provider", choices=["aws", "gcp"], help="Cloud provider to scan")
    parser.add_argument("--region", default="eu-west-1", help="Cloud region")
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--manual", action="store_true", help="Manual entry mode")
    parser.add_argument("--scan", help="Network subnet to scan (e.g., 10.0.0.0/24)")
    parser.add_argument("--org", default="AcmetoCasino", help="Organization name")
    parser.add_argument("--gpe", default="Production", help="GPE environment name")
    parser.add_argument("--gig", type=int, default=3, choices=[1, 2, 3], help="GIG level")
    parser.add_argument("--output", default="markdown", choices=["json", "csv", "markdown"])
    parser.add_argument("--output-file", help="Output file path (default: stdout)")
    parser.add_argument("--version", action="version", version=f"csc-inventory.py v{VERSION}")

    args = parser.parse_args()

    # Create inventory
    inventory = CSCInventory(
        organization=args.org,
        gpe_name=args.gpe,
        inventory_date=datetime.now(timezone.utc).isoformat(),
        version=VERSION,
        gig_level=args.gig,
    )

    # Run discovery
    if args.manual:
        discovery = ManualDiscovery()
        inventory.entries = discovery.discover_all()
    elif args.provider == "aws":
        discovery = AWSDiscovery(region=args.region, profile=args.profile)
        inventory.entries = discovery.discover_all()
    elif args.scan:
        logger.info(f"Network scan not implemented for subnets yet. Use --manual or --provider.")
        sys.exit(1)
    else:
        # Demo mode: generate sample inventory for documentation
        logger.info("No discovery method specified. Generating sample inventory.")
        inventory.entries = _generate_sample_inventory()

    # Generate report
    if args.output == "json":
        report = ReportGenerator.to_json(inventory)
    elif args.output == "csv":
        report = ReportGenerator.to_csv(inventory)
    else:
        report = ReportGenerator.to_markdown(inventory)

    # Output
    if args.output_file:
        Path(args.output_file).write_text(report)
        logger.info(f"Report saved to {args.output_file}")
    else:
        print(report)

    # Summary
    logger.info(
        f"Inventory complete: {inventory.total_count} CSCs "
        f"({inventory.critical_count} Critical, {inventory.high_count} High)"
    )


def _generate_sample_inventory() -> List[CSCEntry]:
    """Generate a realistic sample CSC inventory for an iGaming platform."""
    samples = [
        CSCEntry(
            csc_id="CSC-0001", name="fortuna-rng-primary",
            category=CSCCategory.RNG.value, description="Primary Fortuna PRNG service (AIS-31 certified)",
            risk_level=RiskLevel.CRITICAL.value, ogis_domains=["OGIS-1"],
            hostname="rng-01.prod.acmetocasino.internal", ip_address="10.0.1.10",
            provider="aws", region="eu-west-1", instance_type="c6i.xlarge",
            os_family="linux", owner="Platform Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0002", name="game-engine-cluster",
            category=CSCCategory.GAME_SERVER.value, description="Game logic execution cluster (slots, table games)",
            risk_level=RiskLevel.CRITICAL.value, ogis_domains=["OGIS-1", "OGIS-3"],
            hostname="game-engine.prod.acmetocasino.internal", ip_address="10.0.2.0/24",
            provider="aws", region="eu-west-1", instance_type="m6i.2xlarge",
            os_family="linux", owner="Games Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0003", name="payment-gateway",
            category=CSCCategory.PAYMENT.value, description="Payment processing service (deposits/withdrawals)",
            risk_level=RiskLevel.CRITICAL.value, ogis_domains=["OGIS-2"],
            hostname="payments.prod.acmetocasino.internal", ip_address="10.0.3.10",
            provider="aws", region="eu-west-1", instance_type="m6i.xlarge",
            os_family="linux", owner="Payments Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0004", name="player-db-primary",
            category=CSCCategory.PLAYER_DB.value, description="Player accounts PostgreSQL (PII, KYC data)",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-2", "OGIS-3"],
            hostname="player-db.prod.acmetocasino.internal", ip_address="10.0.4.10",
            provider="aws", region="eu-west-1", instance_type="db.r6g.xlarge",
            os_family="linux", owner="Data Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0005", name="bonus-engine",
            category=CSCCategory.BONUS_ENGINE.value, description="Bonus calculation and campaign management",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-3"],
            hostname="bonus.prod.acmetocasino.internal", ip_address="10.0.5.10",
            provider="aws", region="eu-west-1", instance_type="m6i.large",
            os_family="linux", owner="Marketing Tech",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0006", name="sportsbook-odds-engine",
            category=CSCCategory.SPORTSBOOK.value, description="Real-time odds calculation and bet settlement",
            risk_level=RiskLevel.CRITICAL.value, ogis_domains=["OGIS-1", "OGIS-3"],
            hostname="odds.prod.acmetocasino.internal", ip_address="10.0.6.10",
            provider="aws", region="eu-west-1", instance_type="c6i.2xlarge",
            os_family="linux", owner="Sportsbook Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0007", name="backoffice-admin",
            category=CSCCategory.BACKOFFICE.value, description="Administrative back-office portal",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-2"],
            hostname="bo.prod.acmetocasino.internal", ip_address="10.0.7.10",
            provider="aws", region="eu-west-1", instance_type="m6i.large",
            os_family="linux", owner="Platform Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0008", name="aml-monitoring",
            category=CSCCategory.AML.value, description="AML transaction monitoring and SAR generation",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-3"],
            hostname="aml.prod.acmetocasino.internal", ip_address="10.0.8.10",
            provider="aws", region="eu-west-1", instance_type="m6i.xlarge",
            os_family="linux", owner="Compliance Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0009", name="mobile-api-backend",
            category=CSCCategory.MOBILE_BACKEND.value, description="Mobile app API gateway and backend services",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-4"],
            hostname="mobile-api.prod.acmetocasino.internal", ip_address="10.0.9.10",
            provider="aws", region="eu-west-1", instance_type="m6i.large",
            os_family="linux", owner="Mobile Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
        CSCEntry(
            csc_id="CSC-0010", name="wazuh-siem",
            category=CSCCategory.SIEM.value, description="Wazuh SIEM with iGaming detection rules",
            risk_level=RiskLevel.HIGH.value, ogis_domains=["OGIS-3"],
            hostname="siem.prod.acmetocasino.internal", ip_address="10.0.10.10",
            provider="aws", region="eu-west-1", instance_type="r6i.2xlarge",
            os_family="linux", owner="Security Team",
            encryption_at_rest=True, encryption_in_transit=True,
            backup_enabled=True, monitoring_enabled=True,
        ),
    ]
    return samples


if __name__ == "__main__":
    main()
