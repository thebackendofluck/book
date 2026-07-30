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
Cost Optimization API

This service provides REST API endpoints for cost analysis, optimization recommendations,
and automated cost management for the fraud detection system.
"""

import os
import json
from datetime import datetime, timezone
from typing import List, Optional
import structlog

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .cost_optimization_engine import cost_optimization_engine  # ty:ignore[unresolved-import]

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
    title="Fraud Detection - Cost Optimization Service",
    description="Cost optimization and analysis for fraud detection system",
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
class CostAnalysisRequest(BaseModel):
    period_days: int = 30


class OptimizationImplementationRequest(BaseModel):
    rule_id: str
    resource_ids: List[str]


@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Initialize cost optimization engine on startup"""

    try:
        await cost_optimization_engine.initialize()
        logger.info("Cost optimization service initialized")

    except Exception as e:
        logger.error("Failed to initialize cost optimization service", error=str(e))
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "cost-optimization"
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


@app.post("/api/v1/cost/analysis", response_model=dict)
async def run_cost_analysis(request: CostAnalysisRequest, background_tasks: BackgroundTasks):
    """Run comprehensive cost analysis"""

    try:
        analysis = await cost_optimization_engine.analyze_costs(request.period_days)

        return {
            "analysis_id": analysis.analysis_id,
            "timestamp": analysis.timestamp,
            "period_days": analysis.period_days,
            "total_cost": analysis.total_cost,
            "cost_breakdown": analysis.cost_breakdown,
            "optimization_opportunities": len(analysis.optimization_opportunities),
            "projected_savings": analysis.projected_savings,
            "savings_percentage": (analysis.projected_savings / analysis.total_cost * 100) if analysis.total_cost > 0 else 0,
            "recommendations": analysis.recommendations
        }

    except Exception as e:
        logger.error("Error running cost analysis", error=str(e))
        raise HTTPException(status_code=500, detail=f"Cost analysis failed: {str(e)}")


@app.get("/api/v1/cost/analysis/{analysis_id}")
async def get_cost_analysis(analysis_id: str):
    """Get specific cost analysis results"""

    try:
        # In a real implementation, this would query stored analyses
        # For now, return mock data
        return {
            "analysis_id": analysis_id,
            "status": "not_found",
            "message": "Analysis storage not implemented in this demo"
        }

    except Exception as e:
        logger.error("Error retrieving cost analysis", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve analysis")


@app.get("/api/v1/cost/trends")
async def get_cost_trends(
    period_days: int = Query(90, description="Number of days to analyze trends")
):
    """Get cost trends over time"""

    try:
        trends = await cost_optimization_engine.get_cost_trends(period_days)

        return trends

    except Exception as e:
        logger.error("Error getting cost trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get cost trends: {str(e)}")


@app.get("/api/v1/cost/optimization/rules")
async def list_optimization_rules(
    category: Optional[str] = None,
    enabled: Optional[bool] = None
):
    """List cost optimization rules"""

    rules = []

    for rule in cost_optimization_engine.rules.values():
        # Apply filters
        if category and rule.category != category:
            continue
        if enabled is not None and rule.enabled != enabled:
            continue

        rules.append({
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "category": rule.category,
            "savings_potential_percent": rule.savings_potential_percent,
            "risk_level": rule.risk_level,
            "enabled": rule.enabled
        })

    return {
        "rules": rules,
        "total": len(rules),
        "filters": {
            "category": category,
            "enabled": enabled
        }
    }


@app.post("/api/v1/cost/optimization/implement")
async def implement_optimization(
    request: OptimizationImplementationRequest,
    background_tasks: BackgroundTasks
):
    """Implement a cost optimization action"""

    try:
        result = await cost_optimization_engine.implement_optimization(
            request.rule_id,
            request.resource_ids
        )

        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Error implementing optimization", error=str(e))
        raise HTTPException(status_code=500, detail=f"Optimization implementation failed: {str(e)}")


@app.get("/api/v1/cost/reports/summary")
async def get_cost_summary_report(
    period_days: int = Query(30, description="Number of days to include in report")
):
    """Generate cost summary report"""

    try:
        report = await cost_optimization_engine.generate_cost_report(period_days)

        return report

    except Exception as e:
        logger.error("Error generating cost report", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")


@app.get("/api/v1/cost/savings/potential")
async def get_savings_potential():
    """Get potential cost savings across all categories"""

    try:
        # Run a quick analysis to get current opportunities
        analysis = await cost_optimization_engine.analyze_costs(period_days=7)  # Last week

        # Aggregate savings by category
        savings_by_category = {}
        total_potential_savings = 0

        for opportunity in analysis.optimization_opportunities:
            category = opportunity.get("category", "unknown")
            savings = opportunity.get("potential_savings", 0)

            if category not in savings_by_category:
                savings_by_category[category] = {
                    "opportunities": 0,
                    "potential_savings": 0,
                    "avg_savings_percent": 0
                }

            savings_by_category[category]["opportunities"] += 1
            savings_by_category[category]["potential_savings"] += savings
            total_potential_savings += savings

        # Calculate averages
        for category_data in savings_by_category.values():
            if category_data["opportunities"] > 0:
                category_data["avg_savings_percent"] = (
                    category_data["potential_savings"] / category_data["opportunities"]
                )

        return {
            "total_potential_savings": total_potential_savings,
            "savings_by_category": savings_by_category,
            "analysis_period_days": 7,
            "high_impact_opportunities": len([
                opp for opp in analysis.optimization_opportunities
                if opp.get("potential_savings_percent", 0) > 50
            ])
        }

    except Exception as e:
        logger.error("Error calculating savings potential", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to calculate savings: {str(e)}")


@app.get("/api/v1/cost/optimization/recommendations")
async def get_optimization_recommendations(
    limit: int = Query(10, description="Maximum number of recommendations to return"),
    min_savings_percent: float = Query(0, description="Minimum savings percentage threshold")
):
    """Get prioritized cost optimization recommendations"""

    try:
        # Run analysis to get current recommendations
        analysis = await cost_optimization_engine.analyze_costs(period_days=30)

        # Filter and sort recommendations
        filtered_recommendations = [
            rec for rec in analysis.recommendations
            if rec.get("potential_savings_percent", 0) >= min_savings_percent
        ]

        # Sort by potential savings (descending)
        sorted_recommendations = sorted(
            filtered_recommendations,
            key=lambda x: x.get("potential_savings_percent", 0),
            reverse=True
        )

        return {
            "recommendations": sorted_recommendations[:limit],
            "total_available": len(filtered_recommendations),
            "returned_count": min(limit, len(sorted_recommendations)),
            "min_savings_threshold": min_savings_percent,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error("Error getting optimization recommendations", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get recommendations: {str(e)}")


@app.get("/api/v1/cost/dashboard/summary")
async def get_cost_dashboard_summary():
    """Get cost dashboard summary data"""

    try:
        # Get current analysis
        analysis = await cost_optimization_engine.analyze_costs(period_days=30)

        # Get trends
        trends = await cost_optimization_engine.get_cost_trends(period_days=90)

        # Calculate key metrics
        total_cost = analysis.total_cost
        projected_savings = analysis.projected_savings
        savings_percentage = (projected_savings / total_cost * 100) if total_cost > 0 else 0

        # Cost breakdown by category
        cost_breakdown = analysis.cost_breakdown

        # Top opportunities
        top_opportunities = sorted(
            analysis.optimization_opportunities,
            key=lambda x: x.get("potential_savings_percent", 0),
            reverse=True
        )[:5]

        return {
            "summary": {
                "total_cost": total_cost,
                "projected_savings": projected_savings,
                "savings_percentage": savings_percentage,
                "opportunities_count": len(analysis.optimization_opportunities),
                "high_impact_count": len([opp for opp in analysis.optimization_opportunities
                                        if opp.get("potential_savings_percent", 0) > 50])
            },
            "cost_breakdown": cost_breakdown,
            "trends": {
                "cost_trend_percent": trends.get("cost_trend_percent", 0),
                "savings_trend_percent": trends.get("savings_trend_percent", 0),
                "cost_volatility": trends.get("cost_volatility", 0)
            },
            "top_opportunities": [
                {
                    "rule_name": opp.get("rule_name", ""),
                    "category": opp.get("category", ""),
                    "potential_savings_percent": opp.get("potential_savings_percent", 0),
                    "risk_level": opp.get("risk_level", "")
                }
                for opp in top_opportunities
            ],
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error("Error getting cost dashboard summary", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard summary: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "cost_optimization_api:app",
        host="0.0.0.0",
        port=8085,
        reload=True
    )