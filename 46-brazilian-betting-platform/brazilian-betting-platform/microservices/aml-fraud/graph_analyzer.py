# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AML/Fraud Detection Service — Neo4j Graph Analyzer
====================================================
Builds and queries CPF relationship graphs stored in Neo4j.

Graph schema:
  (:CPF      {cpf, risk_score, first_seen, last_seen})
  (:Device   {fingerprint})
  (:IP       {address})
  (:BankAccount {key})

  [:TRANSACTED_WITH {amount, count, last_ts, last_transaction_id}]
  [:SHARES_DEVICE   {device_id}]
  [:SHARES_IP       {ip, count}]

All queries use 2-hop neighbourhood traversal to balance completeness
vs. performance at scale.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

from database import Neo4jManager, get_neo4j
from models import GraphEdge, GraphNode, GraphRelationship

log = structlog.get_logger(__name__)


class GraphAnalyzer:
    """Async Neo4j graph analysis for CPF relationship networks."""

    def __init__(self, neo4j: Optional[Neo4jManager] = None) -> None:
        self._neo4j = neo4j or get_neo4j()

    # ── Public API ────────────────────────────────────────────────────────────

    async def record_transaction(
        self,
        sender_cpf: str,
        receiver_cpf: str,
        amount: Decimal,
        transaction_id: str,
    ) -> None:
        """Upsert a TRANSACTED_WITH edge between two CPF nodes."""
        if not self._neo4j.is_healthy:
            log.warning("graph_analyzer.neo4j_unavailable", action="record_transaction")
            return

        now = datetime.now(timezone.utc).isoformat()
        query = """
            MERGE (s:CPF {cpf: $sender})
              ON CREATE SET s.first_seen = $ts, s.risk_score = 0.0
              ON MATCH  SET s.last_seen  = $ts
            MERGE (r:CPF {cpf: $receiver})
              ON CREATE SET r.first_seen = $ts, r.risk_score = 0.0
              ON MATCH  SET r.last_seen  = $ts
            MERGE (s)-[e:TRANSACTED_WITH]->(r)
              ON CREATE SET e.count = 1,
                            e.total_amount = $amount,
                            e.last_ts = $ts
              ON MATCH  SET e.count = e.count + 1,
                            e.total_amount = e.total_amount + $amount,
                            e.last_ts = $ts
            SET e.last_transaction_id = $tx_id
        """
        try:
            async with self._neo4j.session() as session:
                await session.run(
                    query,
                    sender=sender_cpf,
                    receiver=receiver_cpf,
                    amount=float(amount),
                    ts=now,
                    tx_id=transaction_id,
                )
            log.debug(
                "graph_analyzer.edge_recorded",
                sender=sender_cpf,
                receiver=receiver_cpf,
                tx_id=transaction_id,
            )
        except Exception as exc:
            log.error(
                "graph_analyzer.record_failed",
                error=str(exc),
                sender=sender_cpf,
                receiver=receiver_cpf,
            )

    async def build_graph(self, cpf: str) -> GraphRelationship:
        """Fetch the full 2-hop neighbourhood for a CPF."""
        if not self._neo4j.is_healthy:
            log.warning("graph_analyzer.neo4j_unavailable", action="build_graph", cpf=cpf)
            return self._empty_graph(cpf)

        try:
            nodes = await self._fetch_nodes(cpf)
            edges = await self._fetch_edges(cpf)
            cluster_risk = await self._compute_cluster_risk(cpf)
            is_mule = await self._detect_mule_network(cpf)

            log.info(
                "graph_analyzer.graph_built",
                cpf=cpf,
                node_count=len(nodes),
                edge_count=len(edges),
                cluster_risk=cluster_risk,
                is_mule=is_mule,
            )

            return GraphRelationship(
                cpf=cpf,
                nodes=nodes,
                edges=edges,
                cluster_risk_score=cluster_risk,
                is_mule_network=is_mule,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            log.error("graph_analyzer.build_failed", cpf=cpf, error=str(exc))
            raise

    async def is_healthy(self) -> bool:
        """Return True if Neo4j is reachable."""
        return self._neo4j.is_healthy

    # ── Internal: node/edge queries ───────────────────────────────────────────

    async def _fetch_nodes(self, cpf: str) -> list[GraphNode]:
        query = """
            MATCH (c:CPF {cpf: $cpf})-[*0..2]-(n)
            RETURN DISTINCT elementId(n) AS node_id,
                            labels(n) AS labels,
                            properties(n) AS props
        """
        nodes: list[GraphNode] = []
        async with self._neo4j.session() as session:
            result = await session.run(query, cpf=cpf)
            async for record in result:
                labels: list[str] = record["labels"]
                raw_props: dict[str, Any] = dict(record["props"])
                string_props = {k: str(v) for k, v in raw_props.items()}
                nodes.append(
                    GraphNode(
                        node_id=str(record["node_id"]),
                        label=labels[0] if labels else "UNKNOWN",
                        properties=string_props,
                    )
                )
        return nodes

    async def _fetch_edges(self, cpf: str) -> list[GraphEdge]:
        query = """
            MATCH (c:CPF {cpf: $cpf})-[r*0..2]-(n)
            UNWIND r AS rel
            RETURN DISTINCT
              coalesce(startNode(rel).cpf, elementId(startNode(rel))) AS src,
              coalesce(endNode(rel).cpf,   elementId(endNode(rel)))   AS tgt,
              type(rel)                                               AS rel_type,
              coalesce(rel.total_amount, 1.0)                         AS weight,
              coalesce(rel.count, 1)                                  AS cnt
        """
        edges: list[GraphEdge] = []
        async with self._neo4j.session() as session:
            result = await session.run(query, cpf=cpf)
            async for record in result:
                edges.append(
                    GraphEdge(
                        source=str(record["src"]),
                        target=str(record["tgt"]),
                        relation=record["rel_type"],
                        weight=float(record["weight"]),
                        properties={"count": str(record["cnt"])},
                    )
                )
        return edges

    # ── Internal: risk / mule detection ──────────────────────────────────────

    async def _compute_cluster_risk(self, cpf: str) -> float:
        """
        Average risk score of 2-hop TRANSACTED_WITH neighbours.
        Amplified slightly when the cluster is large (mule networks).
        """
        query = """
            MATCH (c:CPF {cpf: $cpf})-[:TRANSACTED_WITH*1..2]-(neighbour)
            RETURN avg(coalesce(neighbour.risk_score, 0.0)) AS avg_risk,
                   count(neighbour)                         AS cluster_size
        """
        async with self._neo4j.session() as session:
            result = await session.run(query, cpf=cpf)
            record = await result.single()
            if record is None:
                return 0.0
            avg_risk = float(record["avg_risk"] or 0.0)
            cluster_size = int(record["cluster_size"] or 0)
            # Amplify: large clusters indicate organised money-laundering networks
            return min(1.0, avg_risk + cluster_size / 200.0)

    async def _detect_mule_network(self, cpf: str) -> bool:
        """
        Heuristic: CPF receives from 5+ distinct senders and sends to 2+ distinct receivers.
        This pattern is characteristic of a mule account aggregating and dispersing funds.
        """
        query = """
            MATCH (sender)-[:TRANSACTED_WITH]->(c:CPF {cpf: $cpf})-[:TRANSACTED_WITH]->(receiver)
            WITH count(DISTINCT sender) AS sender_count,
                 count(DISTINCT receiver) AS receiver_count
            RETURN sender_count >= 5 AND receiver_count >= 2 AS is_mule
        """
        async with self._neo4j.session() as session:
            result = await session.run(query, cpf=cpf)
            record = await result.single()
            if record is None:
                return False
            return bool(record["is_mule"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_graph(cpf: str) -> GraphRelationship:
        return GraphRelationship(
            cpf=cpf,
            nodes=[],
            edges=[],
            cluster_risk_score=0.0,
            is_mule_network=False,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
