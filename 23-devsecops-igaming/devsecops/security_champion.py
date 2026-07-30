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
Security Champion Program for iGaming Platforms

Framework for implementing security champion programs to embed
security expertise within development teams.

Usage:
    from security_champion import SecurityChampionProgram

    program = SecurityChampionProgram(slack_client, training_platform)
    await program.initialize_program()
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class ChampionLevel(Enum):
    """Security champion proficiency levels"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class SecurityChampion:
    """Represents a security champion"""
    champion_id: str
    name: str
    email: str
    team: str
    level: ChampionLevel
    certifications: List[str]
    training_hours: int
    code_reviews_completed: int
    vulnerabilities_found: int
    join_date: datetime
    last_activity: datetime


@dataclass
class ProgramStructure:
    """Security champion program configuration"""
    champion_ratio: float = 0.05  # 1 champion per 20 developers
    initial_training_hours: int = 40
    monthly_training_hours: int = 4
    annual_certification: bool = True
    responsibilities: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)


class SecurityChampionProgram:
    """
    Security Champion Program implementation for iGaming platforms.

    Provides framework for:
    - Champion recruitment and onboarding
    - Training program management
    - Security-focused code reviews
    - Vulnerability triage assistance
    - Security awareness initiatives
    """

    def __init__(
        self,
        slack_client: Any,
        training_platform: Any,
        logger: Optional[logging.Logger] = None
    ):
        self.slack_client = slack_client
        self.training_platform = training_platform
        self.logger = logger or logging.getLogger(__name__)
        self.champions: Dict[str, SecurityChampion] = {}
        self.program_metrics: Dict[str, Any] = {}

    async def initialize_program(self) -> Dict[str, Any]:
        """Initialize security champion program"""
        program_structure = ProgramStructure(
            champion_ratio=0.05,
            initial_training_hours=40,
            monthly_training_hours=4,
            annual_certification=True,
            responsibilities=[
                'security_code_reviews',
                'vulnerability_triage',
                'security_training_delivery',
                'threat_modeling_participation',
                'security_tool_evaluation'
            ],
            benefits=[
                'salary_bonus',
                'conference_attendance',
                'certification_reimbursement',
                'career_development',
                'recognition_program'
            ]
        )

        # Set up program components
        await self._recruit_security_champions(program_structure)
        await self._setup_communication_channels()
        await self._create_training_program()

        return {
            'status': 'initialized',
            'champions_recruited': len(self.champions),
            'program_structure': {
                'ratio': program_structure.champion_ratio,
                'training_hours': program_structure.initial_training_hours
            }
        }

    async def run_security_champion_activities(self) -> Dict[str, Any]:
        """Run regular security champion activities"""
        results: Dict[str, Any] = {
            'activities_completed': [],
            'errors': []
        }

        activities = [
            ('weekly_standup', self._weekly_security_standup()),
            ('monthly_review', self._monthly_security_review()),
            ('quarterly_training', self._quarterly_training_session()),
            ('annual_recognition', self._annual_recognition_event())
        ]

        for activity_name, activity_coro in activities:
            try:
                await activity_coro
                results['activities_completed'].append(activity_name)
            except Exception as e:
                self.logger.error(f"Activity {activity_name} failed: {e}")
                results['errors'].append({
                    'activity': activity_name,
                    'error': str(e)
                })

        return results

    async def _weekly_security_standup(self) -> None:
        """Weekly security standup meeting"""
        agenda = [
            'new_vulnerabilities',
            'security_incidents',
            'tool_updates',
            'training_opportunities',
            'recognition_announcements'
        ]

        for champion in self.champions.values():
            await self._send_standup_invite(champion, agenda)

        self.logger.info(
            f"Sent standup invites to {len(self.champions)} champions"
        )

    async def _monthly_security_review(self) -> None:
        """Monthly security metrics review"""
        self.logger.info("Running monthly security review")

    async def _quarterly_training_session(self) -> None:
        """Quarterly training session"""
        self.logger.info("Running quarterly training session")

    async def _annual_recognition_event(self) -> None:
        """Annual recognition event"""
        self.logger.info("Running annual recognition event")

    async def facilitate_security_code_review(
        self,
        pr_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Facilitate security-focused code review"""
        champion_assignments = await self._assign_security_reviewers(pr_data)

        security_feedback: Dict[str, Any] = {
            'vulnerabilities_found': [],
            'security_recommendations': [],
            'compliance_issues': [],
            'champion_reviews': []
        }

        for champion in champion_assignments:
            review_result = await self._conduct_security_review(
                champion,
                pr_data
            )
            security_feedback['champion_reviews'].append(review_result)

            # Aggregate findings
            if 'vulnerabilities' in review_result:
                security_feedback['vulnerabilities_found'].extend(
                    review_result['vulnerabilities']
                )
            if 'recommendations' in review_result:
                security_feedback['security_recommendations'].extend(
                    review_result['recommendations']
                )
            if 'compliance_issues' in review_result:
                security_feedback['compliance_issues'].extend(
                    review_result['compliance_issues']
                )

        return security_feedback

    async def get_champion_metrics(
        self,
        champion_id: str
    ) -> Dict[str, Any]:
        """Get metrics for specific champion"""
        champion = self.champions.get(champion_id)
        if not champion:
            return {'error': 'Champion not found'}

        return {
            'champion_id': champion.champion_id,
            'name': champion.name,
            'level': champion.level.value,
            'training_hours': champion.training_hours,
            'code_reviews': champion.code_reviews_completed,
            'vulnerabilities_found': champion.vulnerabilities_found,
            'certifications': champion.certifications,
            'days_active': (
                datetime.now(timezone.utc) - champion.join_date
            ).days
        }

    async def get_program_metrics(self) -> Dict[str, Any]:
        """Get overall program metrics"""
        total_champions = len(self.champions)
        total_reviews = sum(
            c.code_reviews_completed for c in self.champions.values()
        )
        total_vulns = sum(
            c.vulnerabilities_found for c in self.champions.values()
        )
        total_training = sum(
            c.training_hours for c in self.champions.values()
        )

        level_distribution: Dict[str, int] = {}
        for champion in self.champions.values():
            level = champion.level.value
            level_distribution[level] = level_distribution.get(level, 0) + 1

        return {
            'total_champions': total_champions,
            'total_code_reviews': total_reviews,
            'total_vulnerabilities_found': total_vulns,
            'total_training_hours': total_training,
            'level_distribution': level_distribution,
            'avg_reviews_per_champion': (
                total_reviews / total_champions if total_champions > 0 else 0
            ),
            'avg_vulns_per_champion': (
                total_vulns / total_champions if total_champions > 0 else 0
            )
        }

    async def add_champion(
        self,
        champion_data: Dict[str, Any]
    ) -> SecurityChampion:
        """Add new security champion to program"""
        champion = SecurityChampion(
            champion_id=champion_data.get('id', ''),
            name=champion_data.get('name', ''),
            email=champion_data.get('email', ''),
            team=champion_data.get('team', ''),
            level=ChampionLevel.BEGINNER,
            certifications=[],
            training_hours=0,
            code_reviews_completed=0,
            vulnerabilities_found=0,
            join_date=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc)
        )

        self.champions[champion.champion_id] = champion

        # Send welcome message
        await self._send_welcome_message(champion)

        # Assign initial training
        await self._assign_initial_training(champion)

        self.logger.info(f"Added new champion: {champion.name}")

        return champion

    async def update_champion_activity(
        self,
        champion_id: str,
        activity_type: str,
        activity_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update champion activity metrics"""
        champion = self.champions.get(champion_id)
        if not champion:
            return {'error': 'Champion not found'}

        if activity_type == 'code_review':
            champion.code_reviews_completed += 1
            vulns_found = activity_data.get('vulnerabilities_found', 0)
            champion.vulnerabilities_found += vulns_found

        elif activity_type == 'training':
            hours = activity_data.get('hours', 0)
            champion.training_hours += hours

            # Check for level promotion
            await self._check_level_promotion(champion)

        elif activity_type == 'certification':
            cert = activity_data.get('certification', '')
            if cert and cert not in champion.certifications:
                champion.certifications.append(cert)

        champion.last_activity = datetime.now(timezone.utc)

        return {
            'status': 'updated',
            'champion_id': champion_id,
            'activity': activity_type
        }

    async def _check_level_promotion(
        self,
        champion: SecurityChampion
    ) -> None:
        """Check if champion qualifies for level promotion"""
        promotion_criteria = {
            ChampionLevel.BEGINNER: {
                'training_hours': 40,
                'code_reviews': 10,
                'next_level': ChampionLevel.INTERMEDIATE
            },
            ChampionLevel.INTERMEDIATE: {
                'training_hours': 100,
                'code_reviews': 50,
                'certifications': 1,
                'next_level': ChampionLevel.ADVANCED
            },
            ChampionLevel.ADVANCED: {
                'training_hours': 200,
                'code_reviews': 100,
                'certifications': 2,
                'vulnerabilities': 20,
                'next_level': ChampionLevel.EXPERT
            }
        }

        criteria = promotion_criteria.get(champion.level)
        if not criteria:
            return

        # Extract typed values from criteria with proper type checks
        training_val = criteria.get('training_hours', 0)
        req_training = training_val if isinstance(training_val, int) else 0

        reviews_val = criteria.get('code_reviews', 0)
        req_reviews = reviews_val if isinstance(reviews_val, int) else 0

        certs_val = criteria.get('certifications', 0)
        req_certs = certs_val if isinstance(certs_val, int) else 0

        vulns_val = criteria.get('vulnerabilities', 0)
        req_vulns = vulns_val if isinstance(vulns_val, int) else 0

        next_level = criteria.get('next_level')

        qualifies = True
        if champion.training_hours < req_training:
            qualifies = False
        if champion.code_reviews_completed < req_reviews:
            qualifies = False
        if len(champion.certifications) < req_certs:
            qualifies = False
        if champion.vulnerabilities_found < req_vulns:
            qualifies = False

        if qualifies and isinstance(next_level, ChampionLevel):
            champion.level = next_level
            await self._notify_promotion(champion)
            self.logger.info(
                f"Champion {champion.name} promoted to {champion.level.value}"
            )

    # Stub methods for infrastructure integration
    async def _recruit_security_champions(
        self,
        structure: ProgramStructure
    ) -> None:
        pass

    async def _setup_communication_channels(self) -> None:
        pass

    async def _create_training_program(self) -> None:
        pass

    async def _send_standup_invite(
        self,
        champion: SecurityChampion,
        agenda: List[str]
    ) -> None:
        pass

    async def _assign_security_reviewers(
        self,
        pr_data: Dict[str, Any]
    ) -> List[SecurityChampion]:
        return list(self.champions.values())[:2]

    async def _conduct_security_review(
        self,
        champion: SecurityChampion,
        pr_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            'reviewer': champion.champion_id,
            'vulnerabilities': [],
            'recommendations': [],
            'compliance_issues': []
        }

    async def _send_welcome_message(
        self,
        champion: SecurityChampion
    ) -> None:
        pass

    async def _assign_initial_training(
        self,
        champion: SecurityChampion
    ) -> None:
        pass

    async def _notify_promotion(
        self,
        champion: SecurityChampion
    ) -> None:
        pass


# Champion level requirements for documentation
LEVEL_REQUIREMENTS = {
    'BEGINNER': {
        'training_hours': 0,
        'code_reviews': 0,
        'certifications': 0
    },
    'INTERMEDIATE': {
        'training_hours': 40,
        'code_reviews': 10,
        'certifications': 0
    },
    'ADVANCED': {
        'training_hours': 100,
        'code_reviews': 50,
        'certifications': 1
    },
    'EXPERT': {
        'training_hours': 200,
        'code_reviews': 100,
        'certifications': 2,
        'vulnerabilities_found': 20
    }
}
