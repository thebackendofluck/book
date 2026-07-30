# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Compliance Service API

This service provides REST API endpoints for regulatory compliance management,
including GDPR data subject requests, audit logging, and compliance reporting.
"""

import os
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import structlog

import aiohttp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import redis.asyncio as redis

from .compliance_engine import (  # ty:ignore[unresolved-import]
    ComplianceEngine,
    ComplianceRule,
    ComplianceCheck,
    DataSubjectRequest,
    AuditLogEntry,
    compliance_engine
)

logger = structlog.get_logger(__name__)

# Initialize FastAPI app

# Browser origins allowed to call this service. A wildcard combined with
# allow_credentials lets any site read authenticated responses, so the
# origins have to be named.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="Fraud Detection - Compliance Service",
    description="Regulatory compliance management for fraud detection system",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for API
class ComplianceCheckRequest(BaseModel):
    rule_id: str
    context: Optional[dict] = None


class DataSubjectRequestCreate(BaseModel):
    subject_id: str
    request_type: str  # access, rectification, erasure, restriction, portability, objection
    requester_info: dict = {}
    data_scope: Optional[dict] = None


class AuditLogRequest(BaseModel):
    user_id: Optional[str] = None
    action: str
    resource: str
    resource_id: Optional[str] = None
    details: dict = {}
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    compliance_tags: List[str] = []


@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Initialize compliance engine on startup"""

    try:
        await compliance_engine.initialize()
        logger.info("Compliance service initialized")

    except Exception as e:
        logger.error("Failed to initialize compliance service", error=str(e))
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "compliance"
    }


@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""

    return {
        "status": "ready",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint"""

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), {"Content-Type": CONTENT_TYPE_LATEST}


@app.post("/api/v1/compliance/checks", response_model=ComplianceCheck)
async def run_compliance_check(request: ComplianceCheckRequest, background_tasks: BackgroundTasks):
    """Run a compliance check"""

    try:
        check_result = await compliance_engine.run_compliance_check(
            request.rule_id,
            request.context or {}
        )

        return check_result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Error running compliance check", error=str(e))
        raise HTTPException(status_code=500, detail=f"Compliance check failed: {str(e)}")


@app.get("/api/v1/compliance/rules")
async def list_compliance_rules(
    regulation: Optional[str] = None,
    category: Optional[str] = None,
    enabled: Optional[bool] = None
):
    """List compliance rules with optional filtering"""

    rules = []

    for rule in compliance_engine.rules.values():
        # Apply filters
        if regulation and rule.regulation != regulation:
            continue
        if category and rule.category != category:
            continue
        if enabled is not None and rule.enabled != enabled:
            continue

        rules.append({
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "regulation": rule.regulation,
            "category": rule.category,
            "severity": rule.severity,
            "check_type": rule.check_type,
            "frequency": rule.frequency,
            "enabled": rule.enabled
        })

    return {
        "rules": rules,
        "total": len(rules),
        "filters": {
            "regulation": regulation,
            "category": category,
            "enabled": enabled
        }
    }


@app.get("/api/v1/compliance/checks/history")
async def get_compliance_check_history(
    limit: int = Query(100, description="Maximum number of checks to return"),
    regulation: Optional[str] = None,
    status: Optional[str] = None,
    hours: int = Query(24, description="Hours of history to include")
):
    """Get compliance check history"""

    try:
        # This would be implemented to query stored check results
        # For now, return mock data
        return {
            "checks": [],
            "total": 0,
            "filters": {
                "regulation": regulation,
                "status": status,
                "hours": hours,
                "limit": limit
            }
        }

    except Exception as e:
        logger.error("Error retrieving compliance check history", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve check history")


@app.get("/api/v1/compliance/status")
async def get_compliance_status():
    """Get overall compliance status"""

    try:
        status = await compliance_engine.get_compliance_status()

        return status

    except Exception as e:
        logger.error("Error getting compliance status", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get compliance status")


@app.post("/api/v1/gdpr/requests", response_model=DataSubjectRequest)
async def create_data_subject_request(request: DataSubjectRequestCreate):
    """Create a GDPR data subject request"""

    try:
        # Validate request type
        valid_types = ["access", "rectification", "erasure", "restriction", "portability", "objection"]
        if request.request_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid request type. Must be one of: {', '.join(valid_types)}"
            )

        # Create request object
        dsr = DataSubjectRequest(
            request_id=f"DSR-{int(datetime.now(timezone.utc).timestamp())}",
            subject_id=request.subject_id,
            request_type=request.request_type,
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
            requester_info=request.requester_info,
            data_scope=request.data_scope or {}
        )

        # Store request (implementation would save to database)
        # await store_data_subject_request(dsr)

        # Log audit event
        audit_entry = AuditLogEntry(
            entry_id=f"audit_dsr_{dsr.request_id}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=None,  # System-generated
            action="data_subject_request_created",
            resource="data_subject_request",
            resource_id=dsr.request_id,
            details={
                "request_type": dsr.request_type,
                "subject_id": dsr.subject_id
            },
            compliance_tags=["GDPR", "DSR"]
        )

        await compliance_engine.log_audit_event(audit_entry)

        return dsr

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating data subject request", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create request: {str(e)}")


@app.get("/api/v1/gdpr/requests")
async def list_data_subject_requests(
    status: Optional[str] = None,
    subject_id: Optional[str] = None,
    limit: int = Query(50, description="Maximum number of requests to return")
):
    """List GDPR data subject requests"""

    try:
        # Implementation would query database
        # For now, return empty list
        return {
            "requests": [],
            "total": 0,
            "filters": {
                "status": status,
                "subject_id": subject_id,
                "limit": limit
            }
        }

    except Exception as e:
        logger.error("Error listing data subject requests", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list requests")


@app.put("/api/v1/gdpr/requests/{request_id}")
async def update_data_subject_request_status(
    request_id: str,
    status: str,
    completed_at: Optional[str] = None,
    notes: Optional[str] = None
):
    """Update data subject request status"""

    try:
        valid_statuses = ["pending", "processing", "completed", "rejected"]
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )

        # Implementation would update database
        # For now, just log the update

        # Log audit event
        audit_entry = AuditLogEntry(
            entry_id=f"audit_dsr_update_{request_id}_{int(datetime.now(timezone.utc).timestamp())}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="data_subject_request_updated",
            resource="data_subject_request",
            resource_id=request_id,
            details={
                "new_status": status,
                "completed_at": completed_at,
                "notes": notes
            },
            compliance_tags=["GDPR", "DSR"]
        )

        await compliance_engine.log_audit_event(audit_entry)

        return {
            "request_id": request_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message": f"Data subject request {request_id} updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating data subject request", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to update request: {str(e)}")


@app.post("/api/v1/audit/log")
async def log_audit_event(entry: AuditLogRequest):
    """Log an audit event"""

    try:
        audit_entry = AuditLogEntry(
            entry_id=f"audit_{int(datetime.now(timezone.utc).timestamp())}_{entry.action}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=entry.user_id,
            action=entry.action,
            resource=entry.resource,
            resource_id=entry.resource_id,
            details=entry.details,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            compliance_tags=entry.compliance_tags
        )

        await compliance_engine.log_audit_event(audit_entry)

        return {
            "entry_id": audit_entry.entry_id,
            "status": "logged",
            "timestamp": audit_entry.timestamp
        }

    except Exception as e:
        logger.error("Error logging audit event", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to log audit event: {str(e)}")


@app.get("/api/v1/audit/history")
async def get_audit_history(
    limit: int = Query(100, description="Maximum number of entries to return"),
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource: Optional[str] = None,
    hours: int = Query(24, description="Hours of history to include")
):
    """Get audit log history"""

    try:
        # Implementation would query audit logs from database
        # For now, return empty list
        return {
            "entries": [],
            "total": 0,
            "filters": {
                "user_id": user_id,
                "action": action,
                "resource": resource,
                "hours": hours,
                "limit": limit
            }
        }

    except Exception as e:
        logger.error("Error retrieving audit history", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve audit history")


@app.get("/api/v1/compliance/reports/summary")
async def get_compliance_summary_report(
    regulation: Optional[str] = None,
    days: int = Query(30, description="Number of days to include in report")
):
    """Generate compliance summary report"""

    try:
        # Get compliance status
        status = await compliance_engine.get_compliance_status()

        # Generate report data
        report: Dict[str, Any] = {
            "report_type": "compliance_summary",
            "period_days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": status.get("overall_status", "unknown"),
            "regulations": status.get("regulations", {}),
            "critical_issues": status.get("critical_issues", 0),
            "high_issues": status.get("high_issues", 0),
            "last_check": status.get("last_check"),
            "recommendations": []
        }

        # Add recommendations based on status
        if report["critical_issues"] > 0:
            report["recommendations"].append(
                "Immediate action required: Address critical compliance issues"
            )

        if report["overall_status"] == "non_compliant":
            report["recommendations"].append(
                "System is non-compliant. Immediate remediation required."
            )
        elif report["overall_status"] == "at_risk":
            report["recommendations"].append(
                "System compliance at risk. Monitor closely and address issues."
            )

        # Filter by regulation if specified
        if regulation:
            if regulation in report["regulations"]:
                report["regulations"] = {regulation: report["regulations"][regulation]}
            else:
                report["regulations"] = {}

        return report

    except Exception as e:
        logger.error("Error generating compliance report", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8084,
        reload=True
    )