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
Blameless Postmortem Framework for iGaming Platforms

This module provides a structured approach to conducting blameless postmortems
after incidents, focusing on systemic improvements rather than individual blame.

Features:
- Automated postmortem template generation from incident data
- Root cause analysis framework (5 Whys, Fishbone)
- Action item tracking and follow-up
- Lessons learned database
- Regulatory documentation generation
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class PostmortemDocument:
    """Structured postmortem document"""
    incident_id: str
    title: str
    conducted_at: datetime
    participants: List[str]
    timeline_summary: Dict[str, Any]
    root_cause_analysis: Dict[str, Any]
    impact_assessment: Dict[str, Any]
    lessons_learned: List[str]
    action_items: List[Dict[str, Any]] = field(default_factory=list)
    preventive_measures: List[Dict[str, Any]] = field(default_factory=list)
    follow_up_meetings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "conducted_at": self.conducted_at.isoformat(),
            "participants": self.participants,
            "timeline_summary": self.timeline_summary,
            "root_cause_analysis": self.root_cause_analysis,
            "impact_assessment": self.impact_assessment,
            "lessons_learned": self.lessons_learned,
            "action_items": self.action_items,
            "preventive_measures": self.preventive_measures,
            "follow_up_meetings": self.follow_up_meetings,
        }


class PostmortemFramework:
    """
    Blameless postmortem framework for systematic incident learning.

    Follows Google SRE principles for conducting effective postmortems
    that focus on systemic improvements rather than blame.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)

    async def create_postmortem_template(self, incident_id: str) -> PostmortemDocument:
        """
        Create postmortem template from incident data.

        Automatically populates timeline, affected services, and
        generates initial root cause analysis structure.
        """
        incident = await self._fetch_incident_details(incident_id)

        template = PostmortemDocument(
            incident_id=incident_id,
            title=f"Postmortem: {incident['title']}",
            conducted_at=datetime.now(),
            participants=[],
            timeline_summary=self._create_timeline_summary(incident),
            root_cause_analysis={
                "contributing_factors": [],
                "root_cause_category": "",
                "technical_details": {},
                "process_failures": [],
                "human_factors": [],
                "five_whys": [],
            },
            impact_assessment={
                "business_impact": "",
                "user_impact": "",
                "financial_impact": 0,
                "reputational_impact": "",
                "duration_minutes": 0,
                "affected_transactions": 0,
                "regulatory_implications": "",
            },
            lessons_learned=[],
            action_items=[],
            preventive_measures=[],
            follow_up_meetings=[],
        )

        return template

    def _create_timeline_summary(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Create timeline summary grouped by incident phase"""
        timeline = incident.get("timeline", [])

        phases: Dict[str, List[Dict[str, Any]]] = {
            "detection": [],
            "investigation": [],
            "mitigation": [],
            "resolution": [],
        }

        for event in timeline:
            event_type = event.get("event_type", "")
            if "detected" in event_type:
                phases["detection"].append(event)
            elif "acknowledged" in event_type or "investigating" in event_type:
                phases["investigation"].append(event)
            elif "mitigated" in event_type:
                phases["mitigation"].append(event)
            elif "resolved" in event_type or "closed" in event_type:
                phases["resolution"].append(event)

        return {
            "detection_phase": phases["detection"],
            "investigation_phase": phases["investigation"],
            "mitigation_phase": phases["mitigation"],
            "resolution_phase": phases["resolution"],
            "total_duration_minutes": self._calculate_total_duration(timeline),
            "time_to_detect_minutes": self._calculate_phase_duration(phases["detection"]),
            "time_to_mitigate_minutes": self._calculate_phase_duration(phases["mitigation"]),
        }

    def _calculate_total_duration(self, timeline: List[Dict[str, Any]]) -> int:
        """Calculate total incident duration in minutes"""
        if not timeline:
            return 0

        timestamps = []
        for event in timeline:
            try:
                ts_str = event.get("timestamp", "")
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                timestamps.append(ts)
            except (ValueError, AttributeError):
                continue

        if len(timestamps) < 2:
            return 0

        duration = max(timestamps) - min(timestamps)
        return int(duration.total_seconds() / 60)

    def _calculate_phase_duration(self, phase_events: List[Dict[str, Any]]) -> int:
        """Calculate duration of a specific incident phase"""
        if len(phase_events) < 2:
            return 0
        return self._calculate_total_duration(phase_events)

    async def conduct_blameless_postmortem(
        self, postmortem: PostmortemDocument
    ) -> Dict[str, Any]:
        """
        Conduct structured blameless postmortem.

        Returns discussion questions, identified improvements,
        and generated action items.
        """
        questions = self._generate_discussion_questions(postmortem)
        improvements = await self._identify_improvements(postmortem)
        action_items = self._create_action_items(improvements, postmortem)

        return {
            "questions_discussed": questions,
            "improvements_identified": improvements,
            "action_items_created": action_items,
            "follow_up_required": len(action_items) > 0,
        }

    def _generate_discussion_questions(self, postmortem: PostmortemDocument) -> List[str]:
        """Generate blameless discussion questions"""
        questions = [
            "What was the first indication that something was wrong?",
            "How did we detect and assess the incident?",
            "What factors contributed to the incident duration?",
            "What worked well in our response?",
            "What could we have done differently?",
            "What systemic issues does this incident reveal?",
            "How can we prevent similar incidents in the future?",
            "What additional monitoring or alerting would help?",
            "Do we need to update our runbooks or procedures?",
            "What training or knowledge gaps were exposed?",
        ]

        # Add context-specific questions
        if postmortem.impact_assessment.get("user_impact"):
            questions.append(
                "How did this affect our users and what could we have communicated better?"
            )

        if postmortem.impact_assessment.get("financial_impact", 0) > 0:
            questions.append(
                "What was the financial impact and how can we quantify prevention ROI?"
            )

        if postmortem.impact_assessment.get("regulatory_implications"):
            questions.append(
                "What regulatory notifications or documentation are required?"
            )

        return questions

    async def _identify_improvements(
        self, postmortem: PostmortemDocument
    ) -> List[Dict[str, Any]]:
        """Identify systematic improvement opportunities"""
        improvements = []

        # Analyze timeline for bottlenecks
        timeline_analysis = self._analyze_timeline_bottlenecks(postmortem.timeline_summary)
        if timeline_analysis["bottlenecks"]:
            improvements.append({
                "category": "process",
                "title": "Streamline incident response process",
                "description": f"Address bottlenecks in: {', '.join(timeline_analysis['bottlenecks'])}",
                "priority": "high",
            })

        # Check for monitoring gaps
        monitoring_gaps = self._identify_monitoring_gaps(postmortem)
        if monitoring_gaps:
            improvements.append({
                "category": "monitoring",
                "title": "Enhance monitoring and alerting",
                "description": f"Add monitoring for: {', '.join(monitoring_gaps)}",
                "priority": "high",
            })

        # Technical improvements from root cause
        if postmortem.root_cause_analysis.get("technical_details"):
            improvements.append({
                "category": "technical",
                "title": "Address technical root cause",
                "description": "Implement fix for identified technical issues",
                "priority": "high",
            })

        # Process improvements
        if postmortem.root_cause_analysis.get("process_failures"):
            improvements.append({
                "category": "process",
                "title": "Update operational procedures",
                "description": "Revise procedures to prevent process failures",
                "priority": "medium",
            })

        return improvements

    def _analyze_timeline_bottlenecks(
        self, timeline_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Identify bottlenecks in incident response timeline"""
        bottlenecks = []

        # Detection bottleneck: took too long to detect
        detection_time = timeline_summary.get("time_to_detect_minutes", 0)
        if detection_time > 5:  # Target: <5 minutes for critical issues
            bottlenecks.append("detection")

        # Mitigation bottleneck
        mitigation_time = timeline_summary.get("time_to_mitigate_minutes", 0)
        if mitigation_time > 30:  # Target: <30 minutes
            bottlenecks.append("mitigation")

        return {
            "bottlenecks": bottlenecks,
            "detection_time": detection_time,
            "mitigation_time": mitigation_time,
        }

    def _identify_monitoring_gaps(self, postmortem: PostmortemDocument) -> List[str]:
        """Identify gaps in monitoring coverage"""
        gaps = []

        # If detection phase was slow, monitoring may be inadequate
        if postmortem.timeline_summary.get("time_to_detect_minutes", 0) > 5:
            gaps.append("faster alerting for affected services")

        # Check if incident was user-reported vs system-detected
        detection_events = postmortem.timeline_summary.get("detection_phase", [])
        for event in detection_events:
            if "user_report" in event.get("event_type", "").lower():
                gaps.append("proactive monitoring before user impact")

        return gaps

    def _create_action_items(
        self,
        improvements: List[Dict[str, Any]],
        postmortem: PostmortemDocument,
    ) -> List[Dict[str, Any]]:
        """Create actionable items from identified improvements"""
        action_items = []

        for idx, improvement in enumerate(improvements):
            action_items.append({
                "id": f"AI-{postmortem.incident_id}-{idx + 1}",
                "title": improvement["title"],
                "description": improvement["description"],
                "category": improvement["category"],
                "priority": improvement["priority"],
                "status": "open",
                "owner": "",
                "due_date": "",
                "created_at": datetime.now().isoformat(),
            })

        return action_items

    async def _fetch_incident_details(self, incident_id: str) -> Dict[str, Any]:
        """Fetch incident details from storage"""
        # Implementation would fetch from Redis/database
        return {
            "id": incident_id,
            "title": "Sample Incident",
            "timeline": [],
        }

    def generate_regulatory_report(self, postmortem: PostmortemDocument) -> str:
        """
        Generate regulatory-compliant incident report.

        For iGaming platforms, certain incidents must be reported
        to regulatory bodies (UKGC, MGA, etc.) within specific timeframes.
        """
        report = f"""
INCIDENT REPORT - {postmortem.incident_id}
==========================================
Date: {postmortem.conducted_at.strftime('%Y-%m-%d %H:%M:%S')}

INCIDENT SUMMARY
----------------
Title: {postmortem.title}
Duration: {postmortem.timeline_summary.get('total_duration_minutes', 0)} minutes
Affected Users: {postmortem.impact_assessment.get('user_impact', 'Not specified')}
Financial Impact: {postmortem.impact_assessment.get('financial_impact', 0)}

ROOT CAUSE
----------
Category: {postmortem.root_cause_analysis.get('root_cause_category', 'Under investigation')}

CORRECTIVE ACTIONS
------------------
{chr(10).join(f"- {item['title']}" for item in postmortem.action_items)}

PREVENTIVE MEASURES
-------------------
{chr(10).join(f"- {measure.get('description', '')}" for measure in postmortem.preventive_measures)}
"""
        return report


# Root cause categories for iGaming incidents
ROOT_CAUSE_CATEGORIES = [
    "infrastructure_failure",
    "software_bug",
    "configuration_error",
    "capacity_issue",
    "third_party_dependency",
    "security_incident",
    "human_error",
    "process_failure",
    "monitoring_gap",
    "communication_failure",
]
