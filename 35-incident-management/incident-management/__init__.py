# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Incident Management Framework for iGaming Platforms

This module provides enterprise-grade incident management capabilities
specifically designed for online gambling platforms where incidents
can cost millions in revenue and trigger regulatory scrutiny.

Modules:
- incident_response: Automated incident detection and response
- postmortem_framework: Blameless postmortem and learning system
- maintenance_management: Proactive maintenance scheduling
- change_management: Enterprise change control system
"""

from .incident_response import (  # ty:ignore[unresolved-import]
    IncidentManagementSystem,
    Incident,
    IncidentSeverity,
    IncidentStatus,
)
from .postmortem_framework import (  # ty:ignore[unresolved-import]
    PostmortemFramework,
    PostmortemDocument,
)
from .maintenance_management import (  # ty:ignore[unresolved-import]
    MaintenanceManagementSystem,
    MaintenanceWindow,
    MaintenanceType,
    MaintenanceStatus,
)
from .change_management import (  # ty:ignore[unresolved-import]
    ChangeManagementSystem,
    ChangeRequest,
    ChangeLevel,
    ChangeStatus,
    ChangeType,
    CriticalAsset,
)

__all__ = [
    # Incident Response
    "IncidentManagementSystem",
    "Incident",
    "IncidentSeverity",
    "IncidentStatus",
    # Postmortem
    "PostmortemFramework",
    "PostmortemDocument",
    # Maintenance
    "MaintenanceManagementSystem",
    "MaintenanceWindow",
    "MaintenanceType",
    "MaintenanceStatus",
    # Change Management
    "ChangeManagementSystem",
    "ChangeRequest",
    "ChangeLevel",
    "ChangeStatus",
    "ChangeType",
    "CriticalAsset",
]
