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
SBOM Generator Module
=====================

Generates Software Bill of Materials in multiple formats:
- SPDX (JSON, Tag-Value)
- CycloneDX (JSON, XML)
"""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

class SBOMFormat(Enum):
    """Supported SBOM formats"""
    SPDX_JSON = "spdx-json"
    SPDX_TAG_VALUE = "spdx-tv"
    CYCLONEDX_JSON = "cyclonedx-json"
    CYCLONEDX_XML = "cyclonedx-xml"


class SBOMGenerator:
    """Generate SBOM documents from license scan results"""

    def __init__(self):
        self.creator_tool = "iGaming-License-Scanner"
        self.creator_version = "1.0.0"

    def generate(
        self,
        licenses: List[Any],
        format: SBOMFormat,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate SBOM document.

        Args:
            licenses: List of LicenseInfo objects
            format: Output format
            metadata: Additional metadata

        Returns:
            SBOM as dictionary
        """
        if format == SBOMFormat.SPDX_JSON:
            return self._generate_spdx_json(licenses, metadata)
        elif format == SBOMFormat.CYCLONEDX_JSON:
            return self._generate_cyclonedx_json(licenses, metadata)
        else:
            return self._generate_spdx_json(licenses, metadata)

    def _generate_spdx_json(
        self,
        licenses: List[Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate SPDX 2.3 JSON format SBOM"""
        metadata = metadata or {}
        document_id = f"SPDXRef-DOCUMENT-{uuid.uuid4().hex[:8]}"

        sbom: Dict[str, Any] = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": document_id,
            "name": metadata.get("target", "Unknown Project"),
            "documentNamespace": f"https://spdx.org/spdxdocs/{document_id}",
            "creationInfo": {
                "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "creators": [
                    f"Tool: {self.creator_tool}-{self.creator_version}"
                ],
                "licenseListVersion": "3.21"
            },
            "packages": [],
            "relationships": [],
            "hasExtractedLicensingInfos": []
        }

        # Add packages
        for idx, lic in enumerate(licenses):
            package_id = f"SPDXRef-Package-{idx}"
            package = {
                "SPDXID": package_id,
                "name": lic.package_name if hasattr(lic, 'package_name') else str(lic),
                "versionInfo": lic.package_version if hasattr(lic, 'package_version') else "unknown",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": lic.spdx_id if hasattr(lic, 'spdx_id') else "NOASSERTION",
                "licenseDeclared": lic.spdx_id if hasattr(lic, 'spdx_id') else "NOASSERTION",
                "copyrightText": "NOASSERTION"
            }
            sbom["packages"].append(package)

            # Add relationship
            sbom["relationships"].append({
                "spdxElementId": document_id,
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": package_id
            })

        return sbom

    def _generate_cyclonedx_json(
        self,
        licenses: List[Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate CycloneDX 1.5 JSON format SBOM"""
        metadata = metadata or {}

        sbom: Dict[str, Any] = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": f"urn:uuid:{uuid.uuid4()}",
            "version": 1,
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "tools": [{
                    "vendor": "iGaming",
                    "name": self.creator_tool,
                    "version": self.creator_version
                }],
                "component": {
                    "type": "application",
                    "name": metadata.get("target", "Unknown"),
                    "version": "1.0.0"
                }
            },
            "components": []
        }

        # Add components
        for lic in licenses:
            component = {
                "type": "library",
                "name": lic.package_name if hasattr(lic, 'package_name') else str(lic),
                "version": lic.package_version if hasattr(lic, 'package_version') else "unknown",
                "licenses": [{
                    "license": {
                        "id": lic.spdx_id if hasattr(lic, 'spdx_id') else "NOASSERTION"
                    }
                }],
                "purl": f"pkg:{lic.package_type}/{lic.package_name}@{lic.package_version}"
                    if hasattr(lic, 'package_type') else None
            }
            sbom["components"].append(component)

        return sbom
