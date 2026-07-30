#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
License Policy Engine
=====================

Defines and enforces software license policies for iGaming operations.
Supports customizable rules for different compliance requirements.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk level for license classifications"""
    LOW = "low"           # Permissive licenses (MIT, Apache 2.0, BSD)
    MEDIUM = "medium"     # Weak copyleft, requires review (LGPL, MPL)
    HIGH = "high"         # Strong copyleft (GPL, AGPL)
    CRITICAL = "critical" # Restricted/Non-OSI (SSPL, BSL, Elastic)
    UNKNOWN = "unknown"   # License not recognized


@dataclass
class LicenseClassification:
    """Classification information for a license"""
    spdx_id: str
    name: str
    risk_level: RiskLevel
    category: str  # permissive, weak_copyleft, strong_copyleft, source_available, proprietary
    copyleft: bool = False
    network_copyleft: bool = False  # AGPL-style
    commercial_use: bool = True
    patent_grant: bool = False
    attribution_required: bool = True
    osi_approved: bool = True
    notes: str = ""


# Comprehensive license database
LICENSE_DATABASE: Dict[str, LicenseClassification] = {
    # Permissive Licenses (Low Risk)
    "MIT": LicenseClassification(
        spdx_id="MIT",
        name="MIT License",
        risk_level=RiskLevel.LOW,
        category="permissive",
        patent_grant=False,
        notes="Most permissive, ideal for commercial use"
    ),
    "Apache-2.0": LicenseClassification(
        spdx_id="Apache-2.0",
        name="Apache License 2.0",
        risk_level=RiskLevel.LOW,
        category="permissive",
        patent_grant=True,
        notes="Includes patent grant and retaliation clause"
    ),
    "BSD-2-Clause": LicenseClassification(
        spdx_id="BSD-2-Clause",
        name="BSD 2-Clause License",
        risk_level=RiskLevel.LOW,
        category="permissive",
        notes="Simple permissive license"
    ),
    "BSD-3-Clause": LicenseClassification(
        spdx_id="BSD-3-Clause",
        name="BSD 3-Clause License",
        risk_level=RiskLevel.LOW,
        category="permissive",
        notes="Includes non-endorsement clause"
    ),
    "ISC": LicenseClassification(
        spdx_id="ISC",
        name="ISC License",
        risk_level=RiskLevel.LOW,
        category="permissive",
        notes="Functionally equivalent to MIT"
    ),
    "CC0-1.0": LicenseClassification(
        spdx_id="CC0-1.0",
        name="Creative Commons Zero v1.0",
        risk_level=RiskLevel.LOW,
        category="permissive",
        attribution_required=False,
        notes="Public domain dedication"
    ),
    "Unlicense": LicenseClassification(
        spdx_id="Unlicense",
        name="The Unlicense",
        risk_level=RiskLevel.LOW,
        category="permissive",
        attribution_required=False,
        notes="Public domain equivalent"
    ),

    # Weak Copyleft (Medium Risk)
    "LGPL-2.1-only": LicenseClassification(
        spdx_id="LGPL-2.1-only",
        name="GNU Lesser General Public License v2.1",
        risk_level=RiskLevel.MEDIUM,
        category="weak_copyleft",
        copyleft=True,
        notes="Dynamic linking to proprietary allowed"
    ),
    "LGPL-3.0-only": LicenseClassification(
        spdx_id="LGPL-3.0-only",
        name="GNU Lesser General Public License v3.0",
        risk_level=RiskLevel.MEDIUM,
        category="weak_copyleft",
        copyleft=True,
        patent_grant=True,
        notes="Dynamic linking allowed, includes patent grant"
    ),
    "MPL-2.0": LicenseClassification(
        spdx_id="MPL-2.0",
        name="Mozilla Public License 2.0",
        risk_level=RiskLevel.MEDIUM,
        category="weak_copyleft",
        copyleft=True,
        patent_grant=True,
        notes="File-level copyleft, can combine with proprietary"
    ),
    "EPL-1.0": LicenseClassification(
        spdx_id="EPL-1.0",
        name="Eclipse Public License 1.0",
        risk_level=RiskLevel.MEDIUM,
        category="weak_copyleft",
        copyleft=True,
        patent_grant=True,
        notes="Common in Java ecosystem"
    ),
    "EPL-2.0": LicenseClassification(
        spdx_id="EPL-2.0",
        name="Eclipse Public License 2.0",
        risk_level=RiskLevel.MEDIUM,
        category="weak_copyleft",
        copyleft=True,
        patent_grant=True,
        notes="GPL-compatible option available"
    ),

    # Strong Copyleft (High Risk)
    "GPL-2.0-only": LicenseClassification(
        spdx_id="GPL-2.0-only",
        name="GNU General Public License v2.0",
        risk_level=RiskLevel.HIGH,
        category="strong_copyleft",
        copyleft=True,
        notes="Viral copyleft, derivatives must be GPL"
    ),
    "GPL-3.0-only": LicenseClassification(
        spdx_id="GPL-3.0-only",
        name="GNU General Public License v3.0",
        risk_level=RiskLevel.HIGH,
        category="strong_copyleft",
        copyleft=True,
        patent_grant=True,
        notes="Strong copyleft with patent protection"
    ),
    "AGPL-3.0-only": LicenseClassification(
        spdx_id="AGPL-3.0-only",
        name="GNU Affero General Public License v3.0",
        risk_level=RiskLevel.HIGH,
        category="strong_copyleft",
        copyleft=True,
        network_copyleft=True,
        patent_grant=True,
        notes="Network copyleft - source disclosure for SaaS"
    ),

    # Source-Available (Critical Risk)
    "SSPL-1.0": LicenseClassification(
        spdx_id="SSPL-1.0",
        name="Server Side Public License v1",
        risk_level=RiskLevel.CRITICAL,
        category="source_available",
        copyleft=True,
        network_copyleft=True,
        osi_approved=False,
        notes="MongoDB license - NOT open source, restricts cloud use"
    ),
    "BSL-1.1": LicenseClassification(
        spdx_id="BSL-1.1",
        name="Business Source License 1.1",
        risk_level=RiskLevel.CRITICAL,
        category="source_available",
        commercial_use=False,  # Restricted for large companies
        osi_approved=False,
        notes="Time-delayed open source, production use may require license"
    ),
    "Elastic-2.0": LicenseClassification(
        spdx_id="Elastic-2.0",
        name="Elastic License 2.0",
        risk_level=RiskLevel.CRITICAL,
        category="source_available",
        osi_approved=False,
        notes="Restricts managed service offerings"
    ),
}


@dataclass
class PolicyResult:
    """Result of policy evaluation"""
    license_id: str
    is_allowed: bool
    is_violation: bool
    requires_review: bool
    risk_level: RiskLevel
    reason: str
    recommendation: str


@dataclass
class LicensePolicy:
    """License policy configuration"""
    name: str = "default"
    description: str = "Default iGaming license policy"
    allowed_licenses: Set[str] = field(default_factory=set)
    denied_licenses: Set[str] = field(default_factory=set)
    review_required_licenses: Set[str] = field(default_factory=set)
    max_risk_level: RiskLevel = RiskLevel.MEDIUM
    allow_unknown: bool = False
    require_osi_approved: bool = True
    allow_copyleft: bool = False
    allow_network_copyleft: bool = False
    custom_rules: List[Dict[str, Any]] = field(default_factory=list)


class PolicyEngine:
    """
    Engine for evaluating software licenses against policies.

    Supports:
    - Allowlist/Denylist based policies
    - Risk level thresholds
    - OSI approval requirements
    - Copyleft restrictions
    - Custom rules
    """

    def __init__(
        self,
        allowed: Optional[List[str]] = None,
        denied: Optional[List[str]] = None,
        review_required: Optional[List[str]] = None,
        policy: Optional[LicensePolicy] = None
    ):
        """
        Initialize policy engine.

        Args:
            allowed: List of allowed license SPDX IDs
            denied: List of denied license SPDX IDs
            review_required: List of licenses requiring review
            policy: Complete policy configuration
        """
        if policy:
            self.policy = policy
        else:
            self.policy = LicensePolicy(
                allowed_licenses=set(allowed or []),
                denied_licenses=set(denied or []),
                review_required_licenses=set(review_required or [])
            )

        self.license_db = LICENSE_DATABASE.copy()

    def load_policy(self, policy_data: Dict[str, Any]) -> None:
        """Load policy from dictionary"""
        self.policy = LicensePolicy(
            name=policy_data.get("name", "custom"),
            description=policy_data.get("description", ""),
            allowed_licenses=set(policy_data.get("allowed_licenses", [])),
            denied_licenses=set(policy_data.get("denied_licenses", [])),
            review_required_licenses=set(policy_data.get("review_required", [])),
            max_risk_level=RiskLevel(policy_data.get("max_risk_level", "medium")),
            allow_unknown=policy_data.get("allow_unknown", False),
            require_osi_approved=policy_data.get("require_osi_approved", True),
            allow_copyleft=policy_data.get("allow_copyleft", False),
            allow_network_copyleft=policy_data.get("allow_network_copyleft", False),
            custom_rules=policy_data.get("custom_rules", [])
        )

    def load_policy_file(self, path: str) -> None:
        """Load policy from YAML or JSON file"""
        file_path = Path(path)

        with open(file_path, 'r') as f:
            if file_path.suffix in ['.yaml', '.yml']:
                policy_data = yaml.safe_load(f)
            else:
                policy_data = json.load(f)

        self.load_policy(policy_data)
        logger.info(f"Loaded policy '{self.policy.name}' from {path}")

    def get_license_info(self, license_id: str) -> Optional[LicenseClassification]:
        """Get classification for a license"""
        # Normalize license ID
        normalized = self._normalize_license_id(license_id)

        return self.license_db.get(normalized)

    def _normalize_license_id(self, license_id: str) -> str:
        """Normalize license ID for lookup"""
        # Common normalizations
        normalized = license_id.strip()

        # Handle common variations
        variations = {
            "MIT License": "MIT",
            "Apache 2.0": "Apache-2.0",
            "Apache License 2.0": "Apache-2.0",
            "BSD": "BSD-3-Clause",
            "BSD License": "BSD-3-Clause",
            "GPL": "GPL-3.0-only",
            "GPLv2": "GPL-2.0-only",
            "GPLv3": "GPL-3.0-only",
            "LGPL": "LGPL-3.0-only",
            "LGPLv2.1": "LGPL-2.1-only",
            "LGPLv3": "LGPL-3.0-only",
            "AGPL": "AGPL-3.0-only",
            "AGPLv3": "AGPL-3.0-only",
            "MPL": "MPL-2.0",
            "MPL 2.0": "MPL-2.0",
        }

        return variations.get(normalized, normalized)

    def assess_risk(self, license_id: str) -> RiskLevel:
        """Assess risk level for a license"""
        license_info = self.get_license_info(license_id)

        if license_info:
            return license_info.risk_level

        # Unknown licenses are high risk
        return RiskLevel.UNKNOWN

    def evaluate(self, license_info: Any) -> PolicyResult:
        """
        Evaluate a license against the policy.

        Args:
            license_info: LicenseInfo object from scanner

        Returns:
            PolicyResult with evaluation details
        """
        license_id = license_info.spdx_id if hasattr(license_info, 'spdx_id') else str(license_info)
        normalized_id = self._normalize_license_id(license_id)

        # Check explicit allowlist
        if normalized_id in self.policy.allowed_licenses:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=True,
                is_violation=False,
                requires_review=False,
                risk_level=self.assess_risk(normalized_id),
                reason="License is explicitly allowed",
                recommendation="No action required"
            )

        # Check explicit denylist
        if normalized_id in self.policy.denied_licenses:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=False,
                is_violation=True,
                requires_review=False,
                risk_level=self.assess_risk(normalized_id),
                reason="License is explicitly denied by policy",
                recommendation="Replace dependency with an alternatively licensed package"
            )

        # Check review required list
        if normalized_id in self.policy.review_required_licenses:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=True,
                is_violation=False,
                requires_review=True,
                risk_level=self.assess_risk(normalized_id),
                reason="License requires legal review before use",
                recommendation="Submit for legal review before production deployment"
            )

        # Get license classification
        classification = self.get_license_info(normalized_id)

        if not classification:
            # Unknown license
            if self.policy.allow_unknown:
                return PolicyResult(
                    license_id=normalized_id,
                    is_allowed=True,
                    is_violation=False,
                    requires_review=True,
                    risk_level=RiskLevel.UNKNOWN,
                    reason="Unknown license - requires review",
                    recommendation="Identify the license and add to policy"
                )
            else:
                return PolicyResult(
                    license_id=normalized_id,
                    is_allowed=False,
                    is_violation=True,
                    requires_review=False,
                    risk_level=RiskLevel.UNKNOWN,
                    reason="Unknown license not allowed by policy",
                    recommendation="Identify the license or replace the dependency"
                )

        # Check OSI approval
        if self.policy.require_osi_approved and not classification.osi_approved:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=False,
                is_violation=True,
                requires_review=False,
                risk_level=classification.risk_level,
                reason=f"License is not OSI-approved: {classification.notes}",
                recommendation="Use an OSI-approved alternative"
            )

        # Check copyleft restrictions
        if classification.copyleft and not self.policy.allow_copyleft:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=False,
                is_violation=True,
                requires_review=False,
                risk_level=classification.risk_level,
                reason="Copyleft license not allowed by policy",
                recommendation="Replace with a permissively licensed alternative"
            )

        # Check network copyleft (AGPL)
        if classification.network_copyleft and not self.policy.allow_network_copyleft:
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=False,
                is_violation=True,
                requires_review=False,
                risk_level=classification.risk_level,
                reason="Network copyleft (AGPL-style) not allowed for SaaS deployment",
                recommendation="This license requires source disclosure for network services"
            )

        # Check risk level
        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        if risk_order.index(classification.risk_level) > risk_order.index(self.policy.max_risk_level):
            return PolicyResult(
                license_id=normalized_id,
                is_allowed=False,
                is_violation=True,
                requires_review=False,
                risk_level=classification.risk_level,
                reason=f"License risk level ({classification.risk_level.value}) exceeds policy maximum ({self.policy.max_risk_level.value})",
                recommendation="Replace with a lower-risk licensed alternative"
            )

        # License passes all checks
        return PolicyResult(
            license_id=normalized_id,
            is_allowed=True,
            is_violation=False,
            requires_review=False,
            risk_level=classification.risk_level,
            reason="License complies with policy",
            recommendation="No action required"
        )

    def evaluate_package(self, package_name: str, license_id: str, version: str = "") -> PolicyResult:
        """Convenience method to evaluate a package by name and license"""
        return self.evaluate(license_id)

    def get_policy_summary(self) -> Dict[str, Any]:
        """Get summary of current policy"""
        return {
            "name": self.policy.name,
            "description": self.policy.description,
            "allowed_count": len(self.policy.allowed_licenses),
            "denied_count": len(self.policy.denied_licenses),
            "review_required_count": len(self.policy.review_required_licenses),
            "max_risk_level": self.policy.max_risk_level.value,
            "require_osi_approved": self.policy.require_osi_approved,
            "allow_copyleft": self.policy.allow_copyleft,
            "allow_network_copyleft": self.policy.allow_network_copyleft,
        }
