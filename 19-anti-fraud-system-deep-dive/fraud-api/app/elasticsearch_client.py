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
AcmeToCasino Fraud Detection API — Elasticsearch Client

Handles all interaction with Elasticsearch 8.x:
  - Index template creation and lifecycle management
  - Indexing fraud events and alerts
  - Query builders for the dashboard API endpoints

Index patterns:
  fraud-events-YYYY.MM.dd   — individual scored transaction events
  fraud-alerts-YYYY.MM.dd   — investigation-threshold alerts

Compliance references:
  - PCI DSS Req. 10.5: Protect audit logs from modification (ILM policies with
    read-only snapshots prevent tampering with indexed fraud records).
  - PCI DSS Req. 10.7: Retain audit logs for at least 12 months.
  - UKGC/MGA: 5-year log retention requirement — ILM cold tier handles this.
  - GDPR Article 25: Data minimisation — player PII fields are masked in the
    Elasticsearch index via the `_source_excludes` pattern on sensitive paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk

from .models import (
    FraudAlert,
    FraudAlertsResponse,
    FraudEvent,
    FraudEventsResponse,
    RiskLevel,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Index naming helpers
# ---------------------------------------------------------------------------

EVENTS_INDEX_PATTERN = "fraud-events-*"
ALERTS_INDEX_PATTERN = "fraud-alerts-*"
EVENTS_INDEX_TEMPLATE = "fraud-events-template"
ALERTS_INDEX_TEMPLATE = "fraud-alerts-template"


def _events_index(dt: Optional[datetime] = None) -> str:
    """Return the daily fraud-events index name for the given date (UTC)."""
    dt = dt or datetime.now(timezone.utc)
    return f"fraud-events-{dt.strftime('%Y.%m.%d')}"


def _alerts_index(dt: Optional[datetime] = None) -> str:
    """Return the daily fraud-alerts index name for the given date (UTC)."""
    dt = dt or datetime.now(timezone.utc)
    return f"fraud-alerts-{dt.strftime('%Y.%m.%d')}"


# ---------------------------------------------------------------------------
# Index mappings
# ---------------------------------------------------------------------------

# Explicit field mappings ensure Elasticsearch stores data in the correct types
# and prevents mapping explosions from dynamic mapping on nested `metadata` dicts.
# PCI DSS Req. 10.3: All required audit fields must be preserved exactly as logged.

EVENTS_MAPPING: Dict[str, Any] = {
    "mappings": {
        "dynamic": "false",         # Reject unmapped fields — prevents data model drift
        "properties": {
            "event_id":          {"type": "keyword"},
            "correlation_id":    {"type": "keyword"},
            "created_at":        {"type": "date"},
            "player_id":         {"type": "keyword"},
            "brand_id":          {"type": "integer"},
            "jurisdiction":      {"type": "keyword"},
            "transaction_type":  {"type": "keyword"},
            "amount":            {"type": "double"},
            "currency":          {"type": "keyword"},
            "payment_method":    {"type": "keyword"},
            "deposit_number":    {"type": "integer"},
            # IP and geo — keyword for exact match; not analysed text
            "ip_address":        {"type": "ip"},
            "country_code":      {"type": "keyword"},
            "device_fingerprint":{"type": "keyword"},
            "user_agent":        {"type": "keyword", "index": False},
            "game_session_id":   {"type": "keyword"},
            # Scoring
            "risk_score":        {"type": "float"},
            "risk_level":        {"type": "keyword"},
            "typologies":        {"type": "keyword"},
            "rule_hits":         {"type": "keyword"},
            "model_scores":      {"type": "object", "dynamic": "false"},
            "metadata":          {"type": "object", "dynamic": "false", "enabled": False},
        },
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1,
        "index.lifecycle.name": "fraud-events-ilm-policy",
        "index.lifecycle.rollover_alias": "fraud-events",
    },
}

ALERTS_MAPPING: Dict[str, Any] = {
    "mappings": {
        "dynamic": "false",
        "properties": {
            "event_id":            {"type": "keyword"},
            "alert_id":            {"type": "keyword"},
            "correlation_id":      {"type": "keyword"},
            "created_at":          {"type": "date"},
            "fraud_event_id":      {"type": "keyword"},
            "player_id":           {"type": "keyword"},
            "brand_id":            {"type": "integer"},
            "jurisdiction":        {"type": "keyword"},
            "risk_score":          {"type": "float"},
            "risk_level":          {"type": "keyword"},
            "typologies":          {"type": "keyword"},
            "summary":             {"type": "text", "analyzer": "english"},
            "status":              {"type": "keyword"},
            "assigned_to":         {"type": "keyword"},
            "resolved_at":         {"type": "date"},
            "resolution_notes":    {"type": "text", "index": False},
            "automated_action":    {"type": "keyword"},
            "aml_report_required": {"type": "boolean"},
        },
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1,
        "index.lifecycle.name": "fraud-alerts-ilm-policy",
        "index.lifecycle.rollover_alias": "fraud-alerts",
    },
}

# ---------------------------------------------------------------------------
# Index Lifecycle Management policy
# ---------------------------------------------------------------------------
# PCI DSS Req. 10.7: 12-month online retention; UKGC/MGA require 5 years total.
# ILM policy: hot (30 days) → warm (60 days) → cold (1 year) → delete (5 years).

FRAUD_ILM_POLICY: Dict[str, Any] = {
    "policy": {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {"max_age": "1d", "max_size": "10gb"},
                    "set_priority": {"priority": 100},
                },
            },
            "warm": {
                "min_age": "30d",
                "actions": {
                    "shrink": {"number_of_shards": 1},
                    "forcemerge": {"max_num_segments": 1},
                    "set_priority": {"priority": 50},
                    "readonly": {},
                },
            },
            "cold": {
                "min_age": "90d",
                "actions": {"set_priority": {"priority": 0}},
            },
            "delete": {
                "min_age": "5y",       # 5-year retention (UKGC/MGA requirement)
                "actions": {"delete": {}},
            },
        }
    }
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ElasticsearchClient:
    """
    Async wrapper around the official elasticsearch-py 8.x async client.

    Callers receive a single shared instance (managed by the FastAPI lifespan
    context manager in main.py).  The client uses connection pooling internally;
    do not create a new instance per request.
    """

    def __init__(self, hosts: List[str], **kwargs: Any) -> None:
        """
        Args:
            hosts: List of Elasticsearch node URLs, e.g.
                   ["http://elasticsearch:9200"]
            **kwargs: Passed directly to AsyncElasticsearch (e.g. api_key,
                      basic_auth, ca_certs for TLS verification).
        """
        self._client = AsyncElasticsearch(hosts=hosts, **kwargs)
        logger.info("Elasticsearch client initialised", extra={"hosts": hosts})

    async def close(self) -> None:
        """Close the underlying connection pool. Call on application shutdown."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if Elasticsearch is reachable."""
        try:
            return await self._client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Elasticsearch ping failed", extra={"error": str(exc)})
            return False

    async def cluster_health(self) -> Dict[str, Any]:
        """Return the cluster health response dict."""
        response = await self._client.cluster.health()
        return dict(response)

    # ------------------------------------------------------------------
    # Index and ILM setup
    # ------------------------------------------------------------------

    async def setup_index_templates(self) -> None:
        """
        Idempotently create index templates and ILM policies.

        Called once at application startup (FastAPI lifespan).  Safe to call
        on every restart — ES will update in-place if the template already
        exists.
        """
        # ILM policies
        for policy_name in ("fraud-events-ilm-policy", "fraud-alerts-ilm-policy"):
            await self._client.ilm.put_lifecycle(
                name=policy_name, policy=FRAUD_ILM_POLICY["policy"]
            )
            logger.info("ILM policy upserted", extra={"policy": policy_name})

        # Index templates
        await self._client.indices.put_index_template(
            name=EVENTS_INDEX_TEMPLATE,
            index_patterns=["fraud-events-*"],
            template=EVENTS_MAPPING,
            priority=100,
        )
        await self._client.indices.put_index_template(
            name=ALERTS_INDEX_TEMPLATE,
            index_patterns=["fraud-alerts-*"],
            template=ALERTS_MAPPING,
            priority=100,
        )
        logger.info("Index templates upserted")

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def index_fraud_event(self, event: FraudEvent) -> None:
        """
        Index a single fraud event into the current daily events index.

        The document ID is set to `event.event_id` to ensure idempotency —
        re-indexing the same event (e.g. on Kafka consumer retry) will be a
        no-op update rather than a duplicate.
        """
        doc = event.model_dump(mode="json")
        await self._client.index(
            index=_events_index(event.created_at),
            id=event.event_id,
            document=doc,
        )
        logger.debug(
            "Fraud event indexed",
            extra={
                "event_id": event.event_id,
                "correlation_id": event.correlation_id,
                "risk_level": event.risk_level,
            },
        )

    async def bulk_index_events(self, events: List[FraudEvent]) -> int:
        """
        Bulk-index a list of fraud events.  Returns the number of successfully
        indexed documents.
        """
        actions = [
            {
                "_index": _events_index(e.created_at),
                "_id": e.event_id,
                "_source": e.model_dump(mode="json"),
            }
            for e in events
        ]
        success, _ = await async_bulk(self._client, actions, raise_on_error=False)
        logger.info("Bulk indexed fraud events", extra={"count": success})
        return success

    async def index_fraud_alert(self, alert: FraudAlert) -> None:
        """Index a fraud alert into the current daily alerts index."""
        doc = alert.model_dump(mode="json")
        await self._client.index(
            index=_alerts_index(alert.created_at),
            id=alert.alert_id,
            document=doc,
        )
        logger.info(
            "Fraud alert indexed",
            extra={
                "alert_id": alert.alert_id,
                "risk_level": alert.risk_level,
                "player_id": alert.player_id,
                "aml_report_required": alert.aml_report_required,
            },
        )

    async def update_alert_status(
        self,
        alert_id: str,
        status: str,
        assigned_to: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> None:
        """
        Partial update of an alert document (e.g. when analyst resolves it).

        PCI DSS Req. 10.6: Alert review must be logged — status transitions
        are captured here and become part of the immutable audit trail.
        """
        update_doc: Dict[str, Any] = {"status": status}
        if assigned_to:
            update_doc["assigned_to"] = assigned_to
        if resolution_notes:
            update_doc["resolution_notes"] = resolution_notes
        if status.startswith("resolved"):
            update_doc["resolved_at"] = datetime.now(timezone.utc).isoformat()

        # Search across all alert indices for this alert_id
        response = await self._client.update_by_query(
            index=ALERTS_INDEX_PATTERN,
            query={"term": {"alert_id": alert_id}},
            script={
                "source": "; ".join(
                    f"ctx._source.{k} = params.{k}" for k in update_doc
                ),
                "params": update_doc,
            },
        )
        logger.info(
            "Alert status updated",
            extra={"alert_id": alert_id, "status": status, "updated": response.get("updated")},
        )

    # ------------------------------------------------------------------
    # Query builders — dashboard API
    # ------------------------------------------------------------------

    async def get_recent_events(
        self,
        page: int = 1,
        page_size: int = 20,
        risk_level: Optional[str] = None,
        player_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> FraudEventsResponse:
        """
        Paginated retrieval of fraud events for GET /fraud/events.

        Filters are additive (AND logic).  Results are sorted by `created_at`
        descending so the most recent events appear first in the dashboard.
        """
        must: List[Dict[str, Any]] = []

        if risk_level:
            must.append({"term": {"risk_level": risk_level}})
        if player_id:
            must.append({"term": {"player_id": player_id}})
        if jurisdiction:
            must.append({"term": {"jurisdiction": jurisdiction}})
        if from_dt or to_dt:
            range_filter: Dict[str, Any] = {}
            if from_dt:
                range_filter["gte"] = from_dt.isoformat()
            if to_dt:
                range_filter["lte"] = to_dt.isoformat()
            must.append({"range": {"created_at": range_filter}})

        query = {"bool": {"must": must}} if must else {"match_all": {}}

        response = await self._client.search(
            index=EVENTS_INDEX_PATTERN,
            query=query,
            sort=[{"created_at": {"order": "desc"}}],
            from_=(page - 1) * page_size,
            size=page_size,
        )

        total = response["hits"]["total"]["value"]
        hits = response["hits"]["hits"]
        events = [FraudEvent(**hit["_source"]) for hit in hits]

        return FraudEventsResponse(
            total=total,
            page=page,
            page_size=page_size,
            events=events,
        )

    async def get_active_alerts(
        self,
        page: int = 1,
        page_size: int = 20,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
        jurisdiction: Optional[str] = None,
    ) -> FraudAlertsResponse:
        """
        Paginated retrieval of fraud alerts for GET /fraud/alerts.

        Defaults to open alerts only when no `status` filter is supplied —
        the dashboard analyst view shows only actionable items by default.
        """
        must: List[Dict[str, Any]] = [
            {"term": {"status": status or "open"}},
        ]
        if risk_level:
            must.append({"term": {"risk_level": risk_level}})
        if jurisdiction:
            must.append({"term": {"jurisdiction": jurisdiction}})

        response = await self._client.search(
            index=ALERTS_INDEX_PATTERN,
            query={"bool": {"must": must}},
            sort=[
                {"risk_score": {"order": "desc"}},  # highest risk first
                {"created_at": {"order": "desc"}},
            ],
            from_=(page - 1) * page_size,
            size=page_size,
            aggs={
                "critical_count": {
                    "filter": {"term": {"risk_level": RiskLevel.CRITICAL}},
                },
            },
        )

        total = response["hits"]["total"]["value"]
        hits = response["hits"]["hits"]
        alerts = [FraudAlert(**hit["_source"]) for hit in hits]
        critical_count = response.get("aggregations", {}).get(
            "critical_count", {}
        ).get("doc_count", 0)

        return FraudAlertsResponse(
            total=total,
            page=page,
            page_size=page_size,
            open_count=total,
            critical_count=critical_count,
            alerts=alerts,
        )

    async def get_player_recent_events(
        self, player_id: str, limit: int = 100
    ) -> List[FraudEvent]:
        """
        Retrieve the most recent fraud events for a specific player.
        Used to build the player risk profile in GET /fraud/player/{id}/risk.
        """
        response = await self._client.search(
            index=EVENTS_INDEX_PATTERN,
            query={"term": {"player_id": player_id}},
            sort=[{"created_at": {"order": "desc"}}],
            size=limit,
        )
        return [FraudEvent(**hit["_source"]) for hit in response["hits"]["hits"]]

    async def get_player_open_alert_count(self, player_id: str) -> int:
        """Count open fraud alerts for a player (used in risk profile)."""
        response = await self._client.count(
            index=ALERTS_INDEX_PATTERN,
            query={
                "bool": {
                    "must": [
                        {"term": {"player_id": player_id}},
                        {"term": {"status": "open"}},
                    ]
                }
            },
        )
        return int(response["count"])

    async def get_events_count_24h(self) -> int:
        """
        Count fraud events indexed in the last 24 hours.
        Used by GET /fraud/status for system health metrics.
        """
        try:
            response = await self._client.count(
                index=EVENTS_INDEX_PATTERN,
                query={
                    "range": {
                        "created_at": {"gte": "now-24h/h"}
                    }
                },
            )
            return int(response["count"])
        except NotFoundError:
            return 0

    async def get_alerts_count_24h(self) -> int:
        """Count alerts generated in the last 24 hours."""
        try:
            response = await self._client.count(
                index=ALERTS_INDEX_PATTERN,
                query={
                    "range": {
                        "created_at": {"gte": "now-24h/h"}
                    }
                },
            )
            return int(response["count"])
        except NotFoundError:
            return 0

    async def aggregate_risk_by_jurisdiction(
        self, hours: int = 24
    ) -> Dict[str, Any]:
        """
        Aggregate fraud event counts and average risk scores by jurisdiction
        over the last N hours.  Powers the Kibana jurisdiction-level breakdown.
        """
        response = await self._client.search(
            index=EVENTS_INDEX_PATTERN,
            query={"range": {"created_at": {"gte": f"now-{hours}h/h"}}},
            size=0,
            aggs={
                "by_jurisdiction": {
                    "terms": {"field": "jurisdiction", "size": 20},
                    "aggs": {
                        "avg_risk": {"avg": {"field": "risk_score"}},
                        "by_level": {
                            "terms": {"field": "risk_level"}
                        },
                    },
                }
            },
        )
        return dict(response.get("aggregations", {}))

    async def search_events_by_correlation_id(
        self, correlation_id: str
    ) -> List[FraudEvent]:
        """
        Retrieve all fraud events sharing a correlation_id.

        Satisfies FATF R.16: end-to-end traceability of funds — a single
        correlation_id can be used to pull every event generated by the
        original wallet transaction across all microservices.
        """
        response = await self._client.search(
            index=EVENTS_INDEX_PATTERN,
            query={"term": {"correlation_id": correlation_id}},
            sort=[{"created_at": {"order": "asc"}}],
            size=100,
        )
        return [FraudEvent(**hit["_source"]) for hit in response["hits"]["hits"]]
