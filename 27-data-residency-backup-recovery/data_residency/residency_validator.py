# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Validates that all spoke data is stored in compliant regions.

Reads POLICIES from classification.py, then queries the AWS Config
aggregator (hub account) to check resource placement per spoke account.
Any resource tag with a jurisdiction that conflicts with its host region
is reported as a violation.

Usage:
    python residency_validator.py --profile spoke-nj --jurisdiction nj
    python residency_validator.py --all-spokes --output-format json

AWS Config requirements:
    - AWS Config aggregator enabled in hub account
    - All spoke accounts enrolled as source accounts
    - S3 Object tagging with `data-class` and `jurisdiction` tags enabled
    - RDS instances tagged with `data-class` and `jurisdiction`

Exit codes:
    0 — No violations found
    1 — One or more violations found (non-compliant)
    2 — Execution error (AWS API failure, missing credentials)

Chapter 27 — Data Sovereignty, Residency, and Backup/Recovery
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from classification import (  # type: ignore[import-not-found]
    POLICIES,
    DataClass,
    ResidencyRequirement,
    approved_region,
    get_policy,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    resource_id:    str
    resource_type:  str
    account_id:     str
    actual_region:  str
    required_region: str
    data_class:     str
    jurisdiction:   str
    detail:         str


@dataclass
class ValidationReport:
    run_at:       str
    jurisdiction: str
    account_id:   str
    violations:   list[Violation] = field(default_factory=list)
    checked:      int = 0
    compliant:    int = 0

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    def is_compliant(self) -> bool:
        return self.violation_count == 0


# ---------------------------------------------------------------------------
# AWS Config queries
# ---------------------------------------------------------------------------

def _get_resources_for_type(
    config_client: Any,
    resource_type: str,
    account_id: str | None,
) -> list[dict[str, Any]]:
    """Page through AWS Config resources for a given resource type."""
    resources: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"resourceType": resource_type}
    if account_id:
        kwargs["filters"] = {"accountId": account_id}

    while True:
        try:
            resp = config_client.list_discovered_resources(**kwargs)
        except ClientError as exc:
            logger.warning("Config API error for %s: %s", resource_type, exc)
            break

        resources.extend(resp.get("resourceIdentifiers", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token

    return resources


def _get_resource_tags(
    config_client: Any,
    resource_type: str,
    resource_id: str,
) -> dict[str, str]:
    """Return tags for a specific AWS Config resource."""
    try:
        resp = config_client.batch_get_resource_config(
            resourceKeys=[{"resourceType": resource_type, "resourceId": resource_id}]
        )
        items = resp.get("baseConfigurationItems", [])
        if items:
            return items[0].get("tags", {})
    except ClientError as exc:
        logger.debug("Could not fetch config for %s/%s: %s", resource_type, resource_id, exc)
    return {}


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

CHECKED_RESOURCE_TYPES = [
    "AWS::S3::Bucket",
    "AWS::RDS::DBInstance",
    "AWS::DynamoDB::Table",
    "AWS::EFS::FileSystem",
]


def validate_account(
    session: Any,
    jurisdiction: str,
    account_id: str,
) -> ValidationReport:
    """Check all tagged resources in an account for residency violations."""
    config_client = session.client("config")
    report = ValidationReport(
        run_at=datetime.now(timezone.utc).isoformat(),
        jurisdiction=jurisdiction,
        account_id=account_id,
    )

    for resource_type in CHECKED_RESOURCE_TYPES:
        resources = _get_resources_for_type(config_client, resource_type, account_id)

        for resource in resources:
            resource_id    = resource.get("resourceId", "")
            actual_region  = resource.get("resourceRegion", "")
            tags           = _get_resource_tags(config_client, resource_type, resource_id)

            # Only validate resources that are explicitly tagged with data-class
            raw_class = tags.get("data-class", "").lower().replace("-", "_")
            if not raw_class:
                continue

            report.checked += 1

            # Map tag value to DataClass enum
            try:
                data_class = DataClass(raw_class)
            except ValueError:
                logger.warning(
                    "Unknown data-class tag '%s' on %s/%s — skipping",
                    raw_class, resource_type, resource_id,
                )
                continue

            policy = get_policy(data_class)

            # UNRESTRICTED data has no region constraint
            if policy.requirement == ResidencyRequirement.UNRESTRICTED:
                report.compliant += 1
                continue

            # Determine approved region
            required = approved_region(data_class, jurisdiction)
            if required is None:
                # No mapping for this jurisdiction + data class combination
                logger.warning(
                    "No approved region mapping for %s in %s — skipping %s",
                    data_class.value, jurisdiction, resource_id,
                )
                continue

            if actual_region == required:
                report.compliant += 1
            else:
                report.violations.append(
                    Violation(
                        resource_id=resource_id,
                        resource_type=resource_type,
                        account_id=account_id,
                        actual_region=actual_region,
                        required_region=required,
                        data_class=data_class.value,
                        jurisdiction=jurisdiction,
                        detail=(
                            f"{resource_type} '{resource_id}' in {actual_region} "
                            f"violates {jurisdiction.upper()} residency policy for "
                            f"{data_class.value}: must be in {required}"
                        ),
                    )
                )
                logger.error("VIOLATION: %s", report.violations[-1].detail)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--profile",
        default=None,
        help="AWS CLI profile to use (default: environment credentials)",
    )
    p.add_argument(
        "--jurisdiction",
        choices=["nj", "pa", "mi", "on", "uk", "mt", "ph"],
        help="Jurisdiction code to validate against (required unless --all-spokes)",
    )
    p.add_argument(
        "--account-id",
        default=None,
        help="AWS account ID to check (default: current caller identity)",
    )
    p.add_argument(
        "--all-spokes",
        action="store_true",
        help="Validate all spoke accounts in the Config aggregator",
    )
    p.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    session = boto3.Session(profile_name=args.profile)

    # Resolve account ID
    if args.account_id:
        account_id = args.account_id
    else:
        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]

    if not args.jurisdiction and not args.all_spokes:
        parser.error("Specify --jurisdiction or --all-spokes")

    jurisdictions: list[str]
    if args.all_spokes:
        # Discover spoke jurisdictions from account tags in AWS Organizations
        org = session.client("organizations")
        try:
            accounts = org.list_accounts()["Accounts"]
            jurisdictions = []
            for acct in accounts:
                acct_tags = {
                    t["Key"]: t["Value"]
                    for t in org.list_tags_for_resource(ResourceId=acct["Id"])["Tags"]
                }
                if "jurisdiction" in acct_tags:
                    jurisdictions.append(acct_tags["jurisdiction"])
        except ClientError as exc:
            logger.error("Could not list Organizations accounts: %s", exc)
            return 2
    else:
        jurisdictions = [args.jurisdiction]

    all_reports: list[ValidationReport] = []
    any_violations = False

    for jur in jurisdictions:
        report = validate_account(session, jur, account_id)
        all_reports.append(report)
        if not report.is_compliant():
            any_violations = True

    # Output
    if args.output_format == "json":
        output = [
            {
                "run_at":         r.run_at,
                "jurisdiction":   r.jurisdiction,
                "account_id":     r.account_id,
                "checked":        r.checked,
                "compliant":      r.compliant,
                "violation_count": r.violation_count,
                "violations": [
                    {
                        "resource_id":    v.resource_id,
                        "resource_type":  v.resource_type,
                        "actual_region":  v.actual_region,
                        "required_region": v.required_region,
                        "data_class":     v.data_class,
                        "detail":         v.detail,
                    }
                    for v in r.violations
                ],
            }
            for r in all_reports
        ]
        print(json.dumps(output, indent=2))
    else:
        for r in all_reports:
            print(f"\n=== {r.jurisdiction.upper()} ({r.account_id}) — {r.run_at} ===")
            print(f"Resources checked: {r.checked}")
            print(f"Compliant:         {r.compliant}")
            print(f"Violations:        {r.violation_count}")
            for v in r.violations:
                print(f"  VIOLATION: {v.detail}")
            if r.is_compliant():
                print("  STATUS: COMPLIANT")
            else:
                print("  STATUS: NON-COMPLIANT — remediation required")

    return 1 if any_violations else 0


if __name__ == "__main__":
    sys.exit(main())
