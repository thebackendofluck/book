#!/usr/bin/env python3
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
Elasticsearch Fraud Alert Dashboard — Data Seeder & Kibana Object Generator

Populates an Elasticsearch 8.x instance with realistic synthetic fraud alerts
and creates Kibana Lens dashboard objects via the saved_objects API.

Usage:
    python elastic_fraud_dashboard.py --es-host http://localhost:9200 --kibana-host http://localhost:5601

Requirements:
    Python 3.10+, requests
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SEVERITIES = ["low", "medium", "high", "critical"]
SEV_WEIGHTS = [25, 35, 25, 15]
RISK_BASE = {"low": 15, "medium": 45, "high": 70, "critical": 88}

ALERT_TYPES = [
    "velocity_abuse", "bonus_abuse", "multi_accounting",
    "card_fraud", "identity_fraud", "money_laundering",
    "chip_dumping", "collusion", "bot_detection", "geo_mismatch",
]

STATUSES = ["open", "investigating", "confirmed", "resolved", "false_positive"]
STATUS_WEIGHTS = [20, 25, 20, 25, 10]

COUNTRIES = [
    "BR", "US", "GB", "DE", "FR", "ES", "PT", "NL",
    "AU", "CA", "MX", "AR", "JP", "KR", "IN", "NG", "ZA", "RU", "UA", "PH",
]
CURRENCIES = ["USD", "EUR", "GBP", "BRL", "AUD", "CAD", "JPY"]
ANALYSTS = ["analyst_maria", "analyst_joao", "analyst_carlos", "ml_auto_resolve"]

INDEX_TEMPLATE = {
    "index_patterns": ["fraud-alerts-*"],
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "alert_id": {"type": "keyword"},
                "alert_type": {"type": "keyword"},
                "amount": {"type": "float"},
                "country": {"type": "keyword"},
                "currency": {"type": "keyword"},
                "description": {"type": "text"},
                "device_fingerprint": {"type": "keyword"},
                "ip_address": {"type": "ip"},
                "location": {"type": "geo_point"},
                "resolved_at": {"type": "date"},
                "resolved_by": {"type": "keyword"},
                "risk_score": {"type": "float"},
                "severity": {"type": "keyword"},
                "status": {"type": "keyword"},
                "transaction_id": {"type": "keyword"},
                "user_id": {"type": "keyword"},
                "username": {"type": "keyword"},
            }
        },
    },
}

DATA_VIEW_ID = "fraud-alerts-dataview"

# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------

def generate_alerts(count: int, seed: int = 42) -> list[tuple[str, dict]]:
    random.seed(seed)
    now = datetime.now(timezone.utc)
    users = [f"player_{i:04d}" for i in range(1, 201)]
    alerts = []

    for i in range(count):
        hours_ago = min(random.expovariate(1 / 168), 720)
        ts = now - timedelta(hours=hours_ago)
        sev = random.choices(SEVERITIES, weights=SEV_WEIGHTS, k=1)[0]
        risk = min(100, max(1, RISK_BASE[sev] + random.randint(-10, 10)))
        atype = random.choice(ALERT_TYPES)
        amt = round(
            random.uniform(10, 50000) if sev in ("high", "critical")
            else random.uniform(5, 5000),
            2,
        )
        country = random.choice(COUNTRIES)
        user = random.choice(users)

        doc = {
            "@timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "alert_id": f"FRD-{20260300 + i:08d}",
            "alert_type": atype,
            "severity": sev,
            "risk_score": risk,
            "amount": amt,
            "currency": random.choice(CURRENCIES),
            "country": country,
            "status": random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0],
            "user_id": hashlib.md5(user.encode()).hexdigest()[:12],
            "username": user,
            "ip_address": f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
            "description": f"{atype.replace('_', ' ').title()} detected for {user} from {country}",
            "device_fingerprint": hashlib.sha256(f"{user}{i}".encode()).hexdigest()[:16],
            "transaction_id": f"TXN-{random.randint(100000, 999999)}",
        }

        if doc["status"] in ("resolved", "false_positive"):
            doc["resolved_at"] = (ts + timedelta(hours=random.randint(1, 48))).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            doc["resolved_by"] = random.choice(ANALYSTS)

        alerts.append((ts.strftime("fraud-alerts-%Y.%m"), doc))

    return alerts


# ---------------------------------------------------------------------------
# Elasticsearch operations
# ---------------------------------------------------------------------------

def setup_template(es: str) -> None:
    r = requests.put(
        f"{es}/_index_template/fraud-alerts-template",
        json=INDEX_TEMPLATE,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    print(f"Index template: {r.json()}")


def bulk_index(es: str, alerts: list[tuple[str, dict]], batch: int = 500) -> int:
    total = 0
    for start in range(0, len(alerts), batch):
        chunk = alerts[start : start + batch]
        lines = []
        for idx, doc in chunk:
            lines.append(json.dumps({"index": {"_index": idx}}))
            lines.append(json.dumps(doc))
        body = "\n".join(lines) + "\n"
        r = requests.post(
            f"{es}/_bulk",
            data=body,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=60,
        )
        r.raise_for_status()
        result = r.json()
        if result.get("errors"):
            errs = [
                it["index"]["error"]
                for it in result["items"]
                if "error" in it.get("index", {})
            ]
            print(f"Bulk errors: {errs[:3]}")
        total += len(chunk)
        print(f"  Indexed {total}/{len(alerts)}")

    requests.post(f"{es}/fraud-alerts-*/_refresh", timeout=30)
    return total


# ---------------------------------------------------------------------------
# Kibana dashboard objects
# ---------------------------------------------------------------------------

def _ref():
    return [
        {
            "id": DATA_VIEW_ID,
            "name": "indexpattern-datasource-layer-layer1",
            "type": "index-pattern",
        }
    ]


def _lens(obj_id, title, vis_type, vis, columns, col_order, query=""):
    return {
        "attributes": {
            "description": "",
            "state": {
                "adHocDataViews": {},
                "datasourceStates": {
                    "formBased": {
                        "currentIndexPatternId": DATA_VIEW_ID,
                        "layers": {
                            "layer1": {
                                "columnOrder": col_order,
                                "columns": columns,
                                "incompleteColumns": {},
                                "indexPatternId": DATA_VIEW_ID,
                            }
                        },
                    }
                },
                "filters": [],
                "internalReferences": _ref(),
                "query": {"language": "kuery", "query": query},
                "visualization": vis,
            },
            "title": title,
            "visualizationType": vis_type,
        },
        "coreMigrationVersion": "8.8.0",
        "id": obj_id,
        "managed": False,
        "references": _ref(),
        "type": "lens",
        "typeMigrationVersion": "8.9.0",
    }


def _count_col(label="Count"):
    return {
        "label": label,
        "dataType": "number",
        "operationType": "count",
        "isBucketed": False,
        "scale": "ratio",
        "sourceField": "___records___",
    }


def _terms_col(label, field, size=10, order_col="metric1"):
    return {
        "label": label,
        "dataType": "string",
        "operationType": "terms",
        "scale": "ordinal",
        "sourceField": field,
        "isBucketed": True,
        "params": {
            "size": size,
            "orderBy": {"type": "column", "columnId": order_col},
            "orderDirection": "desc",
        },
    }


def generate_dashboard_objects() -> list[dict]:
    objects = []

    # 1. Total Alerts metric
    objects.append(
        _lens(
            "fraud-total-alerts", "Total Alerts", "lnsMetric",
            {"layerId": "layer1", "layerType": "data", "metricAccessor": "metric1"},
            {"metric1": {**_count_col("Total Alerts")}},
            ["metric1"],
        )
    )

    # 2. Severity donut
    objects.append(
        _lens(
            "fraud-severity-pie", "Alerts by Severity", "lnsPie",
            {
                "shape": "donut",
                "layers": [{
                    "layerId": "layer1", "layerType": "data",
                    "primaryGroups": ["bucket1"], "metrics": ["metric1"],
                    "numberDisplay": "percent", "categoryDisplay": "default",
                    "legendDisplay": "default",
                }],
            },
            {"bucket1": _terms_col("Severity", "severity", 5), "metric1": _count_col()},
            ["bucket1", "metric1"],
        )
    )

    # 3. High/Critical count
    objects.append(
        _lens(
            "fraud-high-severity-count", "High/Critical Severity Alerts", "lnsMetric",
            {"layerId": "layer1", "layerType": "data", "metricAccessor": "metric1"},
            {"metric1": {**_count_col("High/Critical Alerts")}},
            ["metric1"],
            query="severity: high OR severity: critical",
        )
    )

    # 4. Transaction volume over time
    objects.append(
        _lens(
            "fraud-txn-over-time", "Transaction Volume Over Time", "lnsXY",
            {
                "legend": {"isVisible": True, "position": "right"},
                "preferredSeriesType": "line",
                "layers": [{
                    "layerId": "layer1", "layerType": "data",
                    "accessors": ["metric1"], "xAccessor": "bucket1",
                    "seriesType": "line",
                }],
            },
            {
                "bucket1": {
                    "label": "Timestamp",
                    "dataType": "date",
                    "operationType": "date_histogram",
                    "sourceField": "@timestamp",
                    "isBucketed": True,
                    "scale": "interval",
                    "params": {"interval": "12h"},
                },
                "metric1": _count_col(),
            },
            ["bucket1", "metric1"],
        )
    )

    # 5. Top Alert Types bar
    objects.append(
        _lens(
            "fraud-alert-types-bar", "Top Alert Types", "lnsXY",
            {
                "legend": {"isVisible": True, "position": "right"},
                "valueLabels": "hide",
                "preferredSeriesType": "bar_horizontal",
                "layers": [{
                    "layerId": "layer1", "layerType": "data",
                    "accessors": ["metric1"], "xAccessor": "bucket1",
                    "seriesType": "bar_horizontal",
                }],
            },
            {"bucket1": _terms_col("Alert Type", "alert_type"), "metric1": _count_col()},
            ["bucket1", "metric1"],
        )
    )

    # 6. Risk score histogram
    objects.append(
        _lens(
            "fraud-risk-histogram", "Risk Score Distribution", "lnsXY",
            {
                "legend": {"isVisible": False},
                "preferredSeriesType": "bar",
                "layers": [{
                    "layerId": "layer1", "layerType": "data",
                    "accessors": ["metric1"], "xAccessor": "bucket1",
                    "seriesType": "bar",
                }],
            },
            {
                "bucket1": {
                    "label": "Risk Score",
                    "dataType": "number",
                    "operationType": "range",
                    "sourceField": "risk_score",
                    "isBucketed": True,
                    "scale": "ordinal",
                    "params": {
                        "type": "histogram",
                        "ranges": [{"from": 0, "to": 1000, "label": ""}],
                        "maxBars": "auto",
                    },
                },
                "metric1": _count_col(),
            },
            ["bucket1", "metric1"],
        )
    )

    # 7. Recent Alerts datatable
    objects.append(
        _lens(
            "fraud-recent-alerts-table", "Recent Fraud Alerts", "lnsDatatable",
            {
                "layerId": "layer1", "layerType": "data",
                "columns": [
                    {"columnId": "col_ts"},
                    {"columnId": "col_type"},
                    {"columnId": "col_sev"},
                    {"columnId": "col_risk"},
                    {"columnId": "col_count"},
                ],
                "paging": {"size": 20, "enabled": True},
            },
            {
                "col_ts": {
                    "label": "Time", "dataType": "date",
                    "operationType": "date_histogram", "sourceField": "@timestamp",
                    "isBucketed": True, "scale": "interval",
                    "params": {"interval": "auto"},
                },
                "col_type": _terms_col("Alert Type", "alert_type", 50, "col_count"),
                "col_sev": _terms_col("Severity", "severity", 5, "col_count"),
                "col_risk": {
                    "label": "Max Risk", "dataType": "number",
                    "operationType": "max", "sourceField": "risk_score",
                    "isBucketed": False, "scale": "ratio",
                },
                "col_count": _count_col("Count"),
            },
            ["col_ts", "col_type", "col_sev", "col_risk", "col_count"],
        )
    )

    # 8. Dashboard
    panels = []
    panel_refs = []
    layout = [
        ("fraud-total-alerts", 0, 0, 12, 8),
        ("fraud-high-severity-count", 12, 0, 12, 8),
        ("fraud-severity-pie", 24, 0, 24, 8),
        ("fraud-txn-over-time", 0, 8, 24, 12),
        ("fraud-alert-types-bar", 24, 8, 24, 12),
        ("fraud-risk-histogram", 0, 20, 24, 12),
        ("fraud-recent-alerts-table", 24, 20, 24, 12),
    ]
    for i, (vis_id, x, y, w, h) in enumerate(layout, 1):
        pid = f"panel_{i}"
        panels.append({
            "version": "8.11.0", "type": "lens",
            "gridData": {"x": x, "y": y, "w": w, "h": h, "i": pid},
            "panelIndex": pid,
            "embeddableConfig": {"enhancements": {}},
            "panelRefName": f"panel_{pid}",
        })
        panel_refs.append({"name": f"panel_{pid}", "type": "lens", "id": vis_id})

    dashboard = {
        "attributes": {
            "title": "Fraud Monitoring Dashboard",
            "description": "Real-time fraud monitoring with alerts, transactions, and risk analysis",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps({
                "useMargins": True, "syncColors": False,
                "syncCursor": True, "syncTooltips": False,
                "hidePanelTitles": False,
            }),
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "query": {"query": "", "language": "kuery"}, "filter": [],
                })
            },
            "timeRestore": True, "timeFrom": "now-30d", "timeTo": "now",
            "version": 1,
        },
        "coreMigrationVersion": "8.8.0",
        "id": "fraud-monitoring-dashboard",
        "managed": False,
        "references": panel_refs,
        "type": "dashboard",
        "typeMigrationVersion": "8.9.0",
    }
    objects.append(dashboard)

    return objects


def create_data_view(kibana: str) -> None:
    r = requests.post(
        f"{kibana}/api/data_views/data_view",
        json={
            "data_view": {
                "id": DATA_VIEW_ID,
                "title": "fraud-alerts-*",
                "timeFieldName": "@timestamp",
            },
            "override": True,
        },
        headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
        timeout=30,
    )
    print(f"Data view: {r.status_code}")


def import_dashboard(kibana: str, objects: list[dict]) -> None:
    ndjson = "\n".join(json.dumps(o) for o in objects) + "\n"
    tmp = Path("/tmp/fraud-dashboard.ndjson")
    tmp.write_text(ndjson)

    r = requests.post(
        f"{kibana}/api/saved_objects/_import?overwrite=true",
        headers={"kbn-xsrf": "true"},
        files={"file": ("fraud-dashboard.ndjson", ndjson, "application/x-ndjson")},
        timeout=60,
    )
    result = r.json()
    print(f"Import: success={result.get('success')}, count={result.get('successCount')}")
    if result.get("errors"):
        for err in result["errors"][:3]:
            print(f"  Error: {err['id']} - {err['error']['message']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fraud Alert Dashboard Seeder")
    parser.add_argument("--es-host", default="http://localhost:9200")
    parser.add_argument("--kibana-host", default="http://localhost:5601")
    parser.add_argument("--count", type=int, default=500, help="Number of alerts")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-data", action="store_true", help="Skip data seeding")
    parser.add_argument("--skip-dashboard", action="store_true", help="Skip dashboard creation")
    args = parser.parse_args()

    if not args.skip_data:
        print(f"Setting up index template on {args.es_host}...")
        setup_template(args.es_host)

        print(f"Generating {args.count} fraud alerts...")
        alerts = generate_alerts(args.count, args.seed)

        print("Bulk indexing...")
        total = bulk_index(args.es_host, alerts)
        print(f"Indexed {total} documents")

    if not args.skip_dashboard:
        print(f"\nCreating data view on {args.kibana_host}...")
        create_data_view(args.kibana_host)

        print("Generating dashboard objects...")
        objects = generate_dashboard_objects()

        print(f"Importing {len(objects)} objects...")
        import_dashboard(args.kibana_host, objects)

    print("\nDone. Dashboard URL:")
    print(f"  {args.kibana_host}/app/dashboards#/view/fraud-monitoring-dashboard")


if __name__ == "__main__":
    main()
