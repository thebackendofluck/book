# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 39 — Security Incident Response."""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "incident_response")
)

from incident_detection import (
    IncidentDetectionSystem,
    IncidentResponseTeam,
    SecurityIncidentAnalyzer,
)


class TestSecurityIncidentAnalyzer:
    """Validate forensic analysis produces structured output."""

    def test_analyze_initial_compromise_returns_required_keys(self):
        analyzer = SecurityIncidentAnalyzer()
        forensic_data = {
            "source_ip": "198.51.100.42",
            "affected_systems": ["game-server-01"],
        }
        result = analyzer.analyze_initial_compromise(forensic_data)

        assert "attack_methodology" in result
        assert "timeline" in result
        assert "impact_assessment" in result
        assert "technical_indicators" in result
        assert "attribution_analysis" in result

    def test_attack_methodology_has_vector_info(self):
        analyzer = SecurityIncidentAnalyzer()
        result = analyzer.analyze_initial_compromise({})
        methodology = result["attack_methodology"]

        assert "attack_vector" in methodology
        assert "delivery_method" in methodology
        assert "payload_type" in methodology


class TestIncidentDetectionSystem:
    """Validate response level determination logic."""

    def _make_detector(self):
        return IncidentDetectionSystem(
            siem_integration=None, threat_intelligence=None
        )

    def test_critical_severity_maps_to_critical_response(self):
        detector = self._make_detector()
        level = detector._determine_response_level(0.95, {})
        assert level == "critical"

    def test_high_severity_maps_to_high_response(self):
        detector = self._make_detector()
        level = detector._determine_response_level(0.75, {})
        assert level == "high"

    def test_medium_severity_maps_to_medium_response(self):
        detector = self._make_detector()
        level = detector._determine_response_level(0.55, {})
        assert level == "medium"

    def test_low_severity_maps_to_low_response(self):
        detector = self._make_detector()
        level = detector._determine_response_level(0.3, {})
        assert level == "low"

    def test_recommendations_vary_by_response_level(self):
        detector = self._make_detector()
        critical_recs = detector._generate_initial_recommendations("critical")
        low_recs = detector._generate_initial_recommendations("low")
        assert len(critical_recs) > len(low_recs)


class TestIncidentResponseTeam:
    """Validate team composition logic."""

    def test_critical_team_includes_full_roster(self):
        team = IncidentResponseTeam(team_config={})
        composition = team._determine_team_composition("critical")

        assert composition["incident_commander"] is True
        assert composition["forensics_expert"] is True
        assert composition["legal_counsel"] is True
        assert composition["executive_sponsor"] is True

    def test_medium_team_is_smaller_than_critical(self):
        team = IncidentResponseTeam(team_config={})
        critical = team._determine_team_composition("critical")
        medium = team._determine_team_composition("medium")

        critical_active = sum(1 for v in critical.values() if v is True)
        medium_active = sum(1 for v in medium.values() if v is True)
        assert critical_active > medium_active
