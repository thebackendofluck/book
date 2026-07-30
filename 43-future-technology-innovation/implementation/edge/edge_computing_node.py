#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Edge Computing Node for Latency-Sensitive Game Operations
============================================================

Edge computing node configuration and management for iGaming
platforms. Deploys game logic, player session state, and real-time
odds computation close to players for sub-50ms response times.

Usage:
    python edge_computing_node.py --demo
    python edge_computing_node.py --architecture
    python edge_computing_node.py --deployment-plan --regions us-east,eu-west,ap-southeast
"""

import json
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class EdgeNode:
    id: str
    region: str
    location: str
    provider: str           # cloudflare, aws_wavelength, fastly, akamai
    capabilities: list = field(default_factory=list)
    latency_target_ms: int = 50
    compute_units: int = 0
    memory_gb: int = 0
    storage_gb: int = 0
    status: str = "planned"  # planned, deploying, active, degraded, offline
    player_capacity: int = 0
    current_players: int = 0
    services_deployed: list = field(default_factory=list)


@dataclass
class EdgeService:
    name: str
    description: str
    latency_requirement_ms: int
    compute_requirement: str  # light, medium, heavy
    state_requirements: str   # stateless, session_state, persistent
    data_sync: str           # real_time, eventual, none
    regulatory_constraints: list = field(default_factory=list)


EDGE_SERVICES = [
    EdgeService("game-session-manager", "Player game session state management",
                20, "medium", "session_state", "real_time",
                ["Session data must be encrypted", "Player location verification"]),
    EdgeService("odds-calculator", "Real-time sports odds computation",
                10, "heavy", "stateless", "real_time",
                ["Odds must match central source within 0.5%"]),
    EdgeService("player-action-validator", "Validate bets and game actions",
                15, "light", "stateless", "real_time",
                ["Must validate against central balance", "Anti-fraud checks required"]),
    EdgeService("content-cache", "Game assets, images, and static content",
                5, "light", "stateless", "eventual",
                ["Serve age-gated content based on jurisdiction"]),
    EdgeService("geo-fence-enforcer", "Real-time geolocation compliance",
                10, "light", "stateless", "none",
                ["Must use approved geolocation provider", "Block restricted jurisdictions"]),
    EdgeService("live-stream-relay", "Live casino and sports streaming relay",
                30, "heavy", "stateless", "real_time",
                ["Stream integrity verification", "DRM compliance"]),
    EdgeService("personalization-engine", "Real-time content personalization",
                25, "medium", "session_state", "eventual",
                ["GDPR: local data processing preferred"]),
    EdgeService("responsible-gaming-monitor", "Real-time player behavior monitoring",
                50, "medium", "session_state", "real_time",
                ["Must sync interventions to central within 1s"]),
]

EDGE_REGIONS = [
    EdgeNode("edge-us-east-1", "us-east", "Virginia, USA", "aws_wavelength",
             ["compute", "cache", "streaming"], 30, 8, 16, 100, "active", 50000,
             services_deployed=["game-session-manager", "odds-calculator", "content-cache",
                                 "geo-fence-enforcer"]),
    EdgeNode("edge-us-west-1", "us-west", "Oregon, USA", "cloudflare",
             ["compute", "cache"], 35, 4, 8, 50, "active", 25000),
    EdgeNode("edge-eu-west-1", "eu-west", "London, UK", "aws_wavelength",
             ["compute", "cache", "streaming"], 25, 8, 16, 100, "active", 75000,
             services_deployed=["game-session-manager", "odds-calculator", "content-cache",
                                 "geo-fence-enforcer", "responsible-gaming-monitor"]),
    EdgeNode("edge-eu-central-1", "eu-central", "Frankfurt, Germany", "cloudflare",
             ["compute", "cache"], 30, 4, 8, 50, "active", 30000),
    EdgeNode("edge-ap-southeast-1", "ap-southeast", "Singapore", "aws_wavelength",
             ["compute", "cache"], 40, 4, 8, 50, "planned", 20000),
    EdgeNode("edge-latam-1", "sa-east", "Sao Paulo, Brazil", "cloudflare",
             ["compute", "cache"], 45, 4, 8, 50, "planned", 40000),
    EdgeNode("edge-ap-east-1", "ap-east", "Tokyo, Japan", "cloudflare",
             ["compute", "cache"], 35, 4, 8, 50, "planned", 15000),
]


class EdgeComputingManager:
    """Manage edge computing infrastructure for iGaming."""

    def __init__(self):
        self.nodes = {n.id: n for n in EDGE_REGIONS}
        self.services = {s.name: s for s in EDGE_SERVICES}

    def get_architecture(self) -> dict:
        return {
            "architecture": "Hub-and-Spoke Edge Computing for iGaming",
            "design_principles": [
                "Player-facing latency-sensitive operations at the edge",
                "Financial transactions always validated at central (source of truth)",
                "Game state cached at edge, persisted at central",
                "Regulatory compliance enforced at edge (geofencing)",
                "Responsible gaming monitoring runs at both edge and central",
            ],
            "layers": {
                "edge_layer": {
                    "description": "Deployed in 7+ global PoPs, <50ms from players",
                    "services": [s.name for s in EDGE_SERVICES if s.latency_requirement_ms <= 30],
                    "state": "Session state only, synced to central",
                },
                "regional_layer": {
                    "description": "3 regional hubs (US, EU, APAC) for aggregation",
                    "services": ["player-wallet-service", "bonus-engine", "kyc-service"],
                    "state": "Regional database replicas",
                },
                "central_layer": {
                    "description": "Primary data center (source of truth)",
                    "services": ["payment-processor", "rng-service", "regulatory-reporting",
                                 "data-warehouse", "ml-training"],
                    "state": "Master database, financial ledger",
                },
            },
            "data_flow": {
                "player_bet": [
                    "1. Player submits bet -> Edge (geo-fence + session validation) [10ms]",
                    "2. Edge validates locally -> forwards to Regional [20ms]",
                    "3. Regional checks balance -> confirms to Edge [15ms]",
                    "4. Edge returns confirmation to player [5ms]",
                    "5. Async: Regional writes to Central ledger [eventual]",
                    "Total player-perceived latency: ~50ms",
                ],
                "game_round": [
                    "1. Player action -> Edge session manager [10ms]",
                    "2. Edge requests RNG outcome from Central [30ms]",
                    "3. Central RNG generates + signs outcome [5ms]",
                    "4. Edge receives outcome, updates session [5ms]",
                    "5. Edge returns result to player [5ms]",
                    "Total: ~55ms (RNG always centralized for compliance)",
                ],
            },
            "nodes": [{
                "id": n.id, "region": n.region, "location": n.location,
                "provider": n.provider, "status": n.status,
                "latency_target_ms": n.latency_target_ms,
                "player_capacity": n.player_capacity,
                "services": n.services_deployed,
            } for n in self.nodes.values()],
            "services": [{
                "name": s.name, "latency_req_ms": s.latency_requirement_ms,
                "compute": s.compute_requirement, "state": s.state_requirements,
                "constraints": s.regulatory_constraints,
            } for s in self.services.values()],
        }

    def deployment_plan(self, regions: list[str]) -> dict:
        phases = []
        for i, region in enumerate(regions, 1):
            node = next((n for n in self.nodes.values() if n.region == region), None)
            location = node.location if node else region
            phases.append({
                "phase": i,
                "region": region,
                "location": location,
                "timeline": f"Week {i*2}-{i*2+2}",
                "services": [s.name for s in EDGE_SERVICES
                             if s.compute_requirement != "heavy" or i == 1],
                "steps": [
                    f"Provision edge compute in {location}",
                    "Deploy container runtime (K3s/ECS)",
                    "Configure service mesh and mTLS",
                    "Deploy geo-fence-enforcer first (regulatory requirement)",
                    "Deploy content-cache and test latency",
                    "Deploy game-session-manager with central sync",
                    "Load test with simulated players",
                    "Enable live traffic (canary 5% -> 100%)",
                ],
            })
        return {
            "plan_date": datetime.now(timezone.utc).isoformat(),
            "regions": len(regions),
            "total_timeline_weeks": len(regions) * 2 + 2,
            "phases": phases,
        }


def main():
    parser = argparse.ArgumentParser(description="iGaming Edge Computing Manager")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--architecture", action="store_true")
    parser.add_argument("--deployment-plan", action="store_true")
    parser.add_argument("--regions", type=str, default="us-east,eu-west,ap-southeast")
    args = parser.parse_args()

    mgr = EdgeComputingManager()

    if args.architecture or args.demo:
        arch = mgr.get_architecture()
        print(json.dumps(arch, indent=2))
    elif args.deployment_plan:
        regions = [r.strip() for r in args.regions.split(",")]
        print(json.dumps(mgr.deployment_plan(regions), indent=2))
    else:
        print("Usage: python edge_computing_node.py --demo")


if __name__ == "__main__":
    main()
