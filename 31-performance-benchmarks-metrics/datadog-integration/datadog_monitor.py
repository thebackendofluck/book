#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Datadog Integration Module for iGaming Performance Monitoring
==============================================================

Comprehensive Datadog integration for iGaming platform monitoring.
Provides APM, infrastructure, log, and custom metrics configuration.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import json


class MetricType(Enum):
    """Datadog metric types."""
    GAUGE = "gauge"
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    DISTRIBUTION = "distribution"
    SET = "set"


class AlertPriority(Enum):
    """Alert priority levels."""
    P1 = "P1-Critical"
    P2 = "P2-High"
    P3 = "P3-Medium"
    P4 = "P4-Low"
    P5 = "P5-Info"


@dataclass
class DatadogMetric:
    """Datadog metric definition."""
    name: str
    metric_type: MetricType
    description: str
    unit: str
    tags: List[str] = field(default_factory=list)


@dataclass
class DatadogMonitor:
    """Datadog monitor definition."""
    name: str
    query: str
    message: str
    priority: AlertPriority
    thresholds: Dict[str, float] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


class DatadogIGamingIntegration:
    """
    Comprehensive Datadog integration for iGaming platforms.

    Features:
    - APM tracing configuration
    - Custom metrics for gaming operations
    - Dashboard templates
    - Alert and monitor definitions
    - Log pipeline configuration
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.api_key = self.config.get("api_key", "")
        self.app_key = self.config.get("app_key", "")
        self.site = self.config.get("site", "datadoghq.com")

    def get_agent_configuration(self) -> Dict[str, Any]:
        """
        Get Datadog agent configuration for iGaming infrastructure.

        Returns:
            Dict containing agent configuration.
        """
        return {
            "datadog.yaml": {
                "api_key": "${DD_API_KEY}",
                "site": self.site,
                "hostname": "${HOSTNAME}",
                "tags": [
                    "env:production",
                    "service:igaming-platform",
                    "team:platform-engineering"
                ],
                "logs_enabled": True,
                "apm_config": {
                    "enabled": True,
                    "apm_non_local_traffic": True,
                    "max_traces_per_second": 1000,
                    "env": "production"
                },
                "process_config": {
                    "enabled": "true",
                    "container_collection": {
                        "enabled": True
                    }
                },
                "network_config": {
                    "enabled": True
                },
                "runtime_security_config": {
                    "enabled": True
                }
            },
            "docker_compose_snippet": self._get_docker_compose_snippet(),
            "kubernetes_manifest": self._get_kubernetes_manifest()
        }

    def _get_docker_compose_snippet(self) -> str:
        """Get Docker Compose snippet for Datadog agent."""
        return """
version: '3.8'
services:
  datadog-agent:
    image: gcr.io/datadoghq/agent:7
    container_name: datadog-agent
    environment:
      - DD_API_KEY=${DD_API_KEY}
      - DD_SITE=datadoghq.com
      - DD_APM_ENABLED=true
      - DD_APM_NON_LOCAL_TRAFFIC=true
      - DD_LOGS_ENABLED=true
      - DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true
      - DD_PROCESS_AGENT_ENABLED=true
      - DD_DOGSTATSD_NON_LOCAL_TRAFFIC=true
      - DD_CONTAINER_EXCLUDE="name:datadog-agent"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc/:/host/proc/:ro
      - /sys/fs/cgroup/:/host/sys/fs/cgroup:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
    ports:
      - "8125:8125/udp"
      - "8126:8126"
    networks:
      - igaming-network
"""

    def _get_kubernetes_manifest(self) -> str:
        """Get Kubernetes manifest for Datadog agent."""
        return """
apiVersion: datadoghq.com/v2alpha1
kind: DatadogAgent
metadata:
  name: datadog
  namespace: datadog
spec:
  global:
    clusterName: igaming-prod
    site: datadoghq.com
    credentials:
      apiSecret:
        secretName: datadog-secret
        keyName: api-key
      appSecret:
        secretName: datadog-secret
        keyName: app-key
    tags:
      - "env:production"
      - "service:igaming-platform"
  features:
    apm:
      enabled: true
      hostPortConfig:
        enabled: true
    logCollection:
      enabled: true
      containerCollectAll: true
    npm:
      enabled: true
    processDiscovery:
      enabled: true
    usm:
      enabled: true
"""

    def get_custom_metrics_definition(self) -> Dict[str, List[DatadogMetric]]:
        """
        Get custom metrics definitions for iGaming operations.

        Returns:
            Dict containing categorized metric definitions.
        """
        return {
            "gaming_metrics": [
                DatadogMetric(
                    name="igaming.bets.placed",
                    metric_type=MetricType.COUNTER,
                    description="Number of bets placed",
                    unit="bet",
                    tags=["game_type", "currency", "user_segment"]
                ),
                DatadogMetric(
                    name="igaming.bets.amount",
                    metric_type=MetricType.DISTRIBUTION,
                    description="Bet amount distribution",
                    unit="currency",
                    tags=["game_type", "currency"]
                ),
                DatadogMetric(
                    name="igaming.games.active",
                    metric_type=MetricType.GAUGE,
                    description="Number of active game sessions",
                    unit="session",
                    tags=["game_type", "provider"]
                ),
                DatadogMetric(
                    name="igaming.games.launch_time",
                    metric_type=MetricType.HISTOGRAM,
                    description="Game launch time",
                    unit="millisecond",
                    tags=["game_type", "device_type", "region"]
                ),
                DatadogMetric(
                    name="igaming.jackpot.value",
                    metric_type=MetricType.GAUGE,
                    description="Current jackpot value",
                    unit="currency",
                    tags=["jackpot_id", "game_type"]
                )
            ],
            "payment_metrics": [
                DatadogMetric(
                    name="igaming.deposits.count",
                    metric_type=MetricType.COUNTER,
                    description="Number of deposits",
                    unit="transaction",
                    tags=["payment_method", "currency", "status"]
                ),
                DatadogMetric(
                    name="igaming.deposits.amount",
                    metric_type=MetricType.DISTRIBUTION,
                    description="Deposit amount distribution",
                    unit="currency",
                    tags=["payment_method", "currency"]
                ),
                DatadogMetric(
                    name="igaming.withdrawals.processing_time",
                    metric_type=MetricType.HISTOGRAM,
                    description="Withdrawal processing time",
                    unit="second",
                    tags=["payment_method", "status"]
                ),
                DatadogMetric(
                    name="igaming.payments.fraud_score",
                    metric_type=MetricType.GAUGE,
                    description="Payment fraud risk score",
                    unit="score",
                    tags=["payment_method", "user_segment"]
                )
            ],
            "user_metrics": [
                DatadogMetric(
                    name="igaming.users.active",
                    metric_type=MetricType.GAUGE,
                    description="Number of active users",
                    unit="user",
                    tags=["platform", "region", "user_segment"]
                ),
                DatadogMetric(
                    name="igaming.users.session_duration",
                    metric_type=MetricType.HISTOGRAM,
                    description="User session duration",
                    unit="second",
                    tags=["platform", "game_type"]
                ),
                DatadogMetric(
                    name="igaming.users.balance",
                    metric_type=MetricType.DISTRIBUTION,
                    description="User balance distribution",
                    unit="currency",
                    tags=["currency", "user_segment"]
                )
            ],
            "performance_metrics": [
                DatadogMetric(
                    name="igaming.api.response_time",
                    metric_type=MetricType.HISTOGRAM,
                    description="API response time",
                    unit="millisecond",
                    tags=["endpoint", "method", "status_code"]
                ),
                DatadogMetric(
                    name="igaming.api.error_rate",
                    metric_type=MetricType.GAUGE,
                    description="API error rate",
                    unit="percent",
                    tags=["endpoint", "error_type"]
                ),
                DatadogMetric(
                    name="igaming.websocket.messages",
                    metric_type=MetricType.COUNTER,
                    description="WebSocket messages",
                    unit="message",
                    tags=["direction", "message_type"]
                ),
                DatadogMetric(
                    name="igaming.database.query_time",
                    metric_type=MetricType.HISTOGRAM,
                    description="Database query time",
                    unit="millisecond",
                    tags=["query_type", "database"]
                )
            ]
        }

    def get_monitor_definitions(self) -> List[DatadogMonitor]:
        """
        Get monitor definitions for iGaming platform.

        Returns:
            List of DatadogMonitor definitions.
        """
        return [
            # Critical Monitors
            DatadogMonitor(
                name="[P1] Platform Availability",
                query="avg(last_5m):avg:igaming.api.health{env:production} < 0.999",
                message="""
## Platform Availability Critical
Platform availability has dropped below 99.9%.

**Impact**: Users may be unable to access the platform.

**Actions**:
1. Check service health in Kubernetes
2. Review recent deployments
3. Check database connectivity
4. Escalate to on-call engineer

@pagerduty-igaming-critical
                """,
                priority=AlertPriority.P1,
                thresholds={"critical": 0.999, "warning": 0.9995},
                tags=["team:platform", "service:core"]
            ),
            DatadogMonitor(
                name="[P1] Payment System Failure",
                query="avg(last_2m):sum:igaming.deposits.count{status:failed} / sum:igaming.deposits.count{*} > 0.05",
                message="""
## Payment System Critical Failure
Payment failure rate exceeds 5%.

**Impact**: Users cannot make deposits, direct revenue impact.

**Actions**:
1. Check payment provider status
2. Review error logs in Datadog Logs
3. Contact payment provider if external issue
4. Activate backup payment provider

@pagerduty-payments @slack-payments-alerts
                """,
                priority=AlertPriority.P1,
                thresholds={"critical": 0.05, "warning": 0.02},
                tags=["team:payments", "service:payments"]
            ),
            DatadogMonitor(
                name="[P1] Bet Placement Latency",
                query="avg(last_5m):p95:igaming.api.response_time{endpoint:bet_placement} > 500",
                message="""
## Bet Placement Latency Critical
P95 bet placement latency exceeds 500ms.

**Impact**: Poor user experience, potential bet rejections.

**Actions**:
1. Check database performance
2. Review recent code changes
3. Check for traffic spikes
4. Scale if needed

@pagerduty-gaming @slack-gaming-alerts
                """,
                priority=AlertPriority.P1,
                thresholds={"critical": 500, "warning": 300},
                tags=["team:gaming", "service:betting"]
            ),
            # High Priority Monitors
            DatadogMonitor(
                name="[P2] API Error Rate",
                query="avg(last_10m):sum:igaming.api.error_rate{*} > 1",
                message="""
## API Error Rate High
API error rate exceeds 1%.

**Impact**: Degraded user experience.

**Actions**:
1. Review error logs
2. Check service dependencies
3. Review recent deployments

@slack-platform-alerts
                """,
                priority=AlertPriority.P2,
                thresholds={"critical": 1, "warning": 0.5},
                tags=["team:platform", "service:api"]
            ),
            DatadogMonitor(
                name="[P2] Database Query Latency",
                query="avg(last_5m):p95:igaming.database.query_time{*} > 100",
                message="""
## Database Query Latency High
P95 database query time exceeds 100ms.

**Impact**: Overall API latency increase.

**Actions**:
1. Check slow query logs
2. Review database connections
3. Check for locking issues

@slack-database-alerts
                """,
                priority=AlertPriority.P2,
                thresholds={"critical": 100, "warning": 50},
                tags=["team:database", "service:database"]
            ),
            # Medium Priority Monitors
            DatadogMonitor(
                name="[P3] Memory Usage High",
                query="avg(last_15m):avg:system.mem.pct_usable{service:igaming-*} < 0.15",
                message="""
## Memory Usage High
Available memory below 15%.

**Actions**:
1. Review memory-intensive processes
2. Consider scaling
3. Check for memory leaks

@slack-infrastructure-alerts
                """,
                priority=AlertPriority.P3,
                thresholds={"critical": 0.15, "warning": 0.25},
                tags=["team:infrastructure"]
            ),
            DatadogMonitor(
                name="[P3] Game Provider Latency",
                query="avg(last_10m):p95:igaming.games.launch_time{*} by {provider} > 3000",
                message="""
## Game Provider Latency High
Game launch time exceeds 3 seconds for {{provider.name}}.

**Actions**:
1. Contact game provider
2. Check CDN performance
3. Review network connectivity

@slack-gaming-alerts
                """,
                priority=AlertPriority.P3,
                thresholds={"critical": 3000, "warning": 2000},
                tags=["team:gaming", "service:game-providers"]
            )
        ]

    def get_dashboard_definition(self) -> Dict[str, Any]:
        """
        Get dashboard definition for iGaming monitoring.

        Returns:
            Dict containing dashboard definition.
        """
        return {
            "title": "iGaming Platform Performance Dashboard",
            "description": "Comprehensive performance monitoring for iGaming platform",
            "layout_type": "ordered",
            "widgets": [
                {
                    "title": "Platform Health Overview",
                    "type": "group",
                    "widgets": [
                        {
                            "title": "Service Availability",
                            "type": "query_value",
                            "query": "avg:igaming.api.health{*}",
                            "precision": 2,
                            "conditional_formats": [
                                {"comparator": ">=", "value": 0.999, "palette": "green"},
                                {"comparator": ">=", "value": 0.99, "palette": "yellow"},
                                {"comparator": "<", "value": 0.99, "palette": "red"}
                            ]
                        },
                        {
                            "title": "Active Users",
                            "type": "query_value",
                            "query": "sum:igaming.users.active{*}"
                        },
                        {
                            "title": "Bets per Second",
                            "type": "query_value",
                            "query": "sum:igaming.bets.placed{*}.as_rate()"
                        }
                    ]
                },
                {
                    "title": "API Performance",
                    "type": "group",
                    "widgets": [
                        {
                            "title": "Response Time (p50, p95, p99)",
                            "type": "timeseries",
                            "queries": [
                                "p50:igaming.api.response_time{*}",
                                "p95:igaming.api.response_time{*}",
                                "p99:igaming.api.response_time{*}"
                            ]
                        },
                        {
                            "title": "Error Rate by Endpoint",
                            "type": "timeseries",
                            "query": "sum:igaming.api.error_rate{*} by {endpoint}"
                        },
                        {
                            "title": "Request Rate",
                            "type": "timeseries",
                            "query": "sum:trace.http.request{service:igaming-*}.as_rate()"
                        }
                    ]
                },
                {
                    "title": "Gaming Operations",
                    "type": "group",
                    "widgets": [
                        {
                            "title": "Active Games by Type",
                            "type": "toplist",
                            "query": "sum:igaming.games.active{*} by {game_type}"
                        },
                        {
                            "title": "Game Launch Time by Provider",
                            "type": "heatmap",
                            "query": "avg:igaming.games.launch_time{*} by {provider}"
                        },
                        {
                            "title": "Bet Amount Distribution",
                            "type": "distribution",
                            "query": "igaming.bets.amount{*}"
                        }
                    ]
                },
                {
                    "title": "Infrastructure",
                    "type": "group",
                    "widgets": [
                        {
                            "title": "CPU Usage by Service",
                            "type": "timeseries",
                            "query": "avg:system.cpu.user{service:igaming-*} by {service}"
                        },
                        {
                            "title": "Memory Usage",
                            "type": "timeseries",
                            "query": "avg:system.mem.pct_usable{service:igaming-*} by {service}"
                        },
                        {
                            "title": "Database Connections",
                            "type": "timeseries",
                            "query": "sum:postgresql.connections{*} by {db}"
                        }
                    ]
                }
            ],
            "template_variables": [
                {"name": "env", "default": "production", "prefix": "env"},
                {"name": "service", "default": "*", "prefix": "service"},
                {"name": "region", "default": "*", "prefix": "region"}
            ]
        }

    def get_apm_configuration(self) -> Dict[str, Any]:
        """
        Get APM configuration for different frameworks.

        Returns:
            Dict containing APM configuration for various frameworks.
        """
        return {
            "python_ddtrace": {
                "installation": "pip install ddtrace",
                "instrumentation": """
# Automatic instrumentation
ddtrace-run python app.py

# Or programmatic instrumentation
from ddtrace import tracer, patch_all

# Patch all supported libraries
patch_all()

# Configure tracer
tracer.configure(
    hostname='localhost',
    port=8126,
    env='production',
    service='igaming-api',
    version='1.0.0'
)

# Custom span example
from ddtrace import tracer

@tracer.wrap(service='igaming-api', resource='process_bet')
def process_bet(bet_data):
    with tracer.trace('validate_bet') as span:
        span.set_tag('bet_amount', bet_data['amount'])
        span.set_tag('game_type', bet_data['game_type'])
        # Validation logic
    return result
"""
            },
            "nodejs_ddtrace": {
                "installation": "npm install dd-trace",
                "instrumentation": """
// Initialize tracer before other imports
const tracer = require('dd-trace').init({
    env: 'production',
    service: 'igaming-api',
    version: '1.0.0',
    logInjection: true,
    runtimeMetrics: true
});

// Custom span example
const placeBet = async (betData) => {
    const span = tracer.startSpan('process_bet', {
        tags: {
            'bet.amount': betData.amount,
            'game.type': betData.gameType
        }
    });

    try {
        const result = await processBetLogic(betData);
        span.setTag('bet.result', 'success');
        return result;
    } catch (error) {
        span.setTag('error', true);
        span.setTag('error.message', error.message);
        throw error;
    } finally {
        span.finish();
    }
};
"""
            },
            "java_ddtrace": {
                "installation": "Download dd-java-agent.jar",
                "instrumentation": """
# JVM arguments
java -javaagent:/path/to/dd-java-agent.jar \\
    -Ddd.service=igaming-api \\
    -Ddd.env=production \\
    -Ddd.version=1.0.0 \\
    -Ddd.logs.injection=true \\
    -Ddd.trace.analytics.enabled=true \\
    -jar igaming-api.jar

// Custom span in code
import datadog.trace.api.Trace;
import datadog.trace.api.DDTags;
import io.opentracing.Span;
import io.opentracing.util.GlobalTracer;

public class BettingService {
    @Trace(operationName = "process_bet", resourceName = "BettingService.placeBet")
    public BetResult placeBet(BetRequest request) {
        Span span = GlobalTracer.get().activeSpan();
        span.setTag("bet.amount", request.getAmount());
        span.setTag("game.type", request.getGameType());

        return processInternal(request);
    }
}
"""
            }
        }

    def get_log_pipeline_configuration(self) -> Dict[str, Any]:
        """
        Get log pipeline configuration for structured logging.

        Returns:
            Dict containing log pipeline configuration.
        """
        return {
            "log_format": {
                "json_format": {
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "service": "igaming-api",
                    "logger": "%(name)s",
                    "message": "%(message)s",
                    "dd.trace_id": "%(dd.trace_id)s",
                    "dd.span_id": "%(dd.span_id)s"
                }
            },
            "pipelines": [
                {
                    "name": "iGaming API Logs",
                    "filter": "service:igaming-api",
                    "processors": [
                        {
                            "type": "grok-parser",
                            "source": "message",
                            "rules": [
                                "bet_placed %{DATA:action} user=%{DATA:user_id} amount=%{NUMBER:amount}"
                            ]
                        },
                        {
                            "type": "category-processor",
                            "target": "log_category",
                            "categories": [
                                {"name": "bet", "filter": "action:bet_*"},
                                {"name": "payment", "filter": "action:payment_*"},
                                {"name": "auth", "filter": "action:login OR action:logout"}
                            ]
                        },
                        {
                            "type": "attribute-remapper",
                            "source": "user_id",
                            "target": "usr.id"
                        }
                    ]
                }
            ],
            "indexes": [
                {
                    "name": "igaming-main",
                    "filter": "service:igaming-*",
                    "retention_days": 15
                },
                {
                    "name": "igaming-security",
                    "filter": "service:igaming-* AND (status:error OR @log_category:auth)",
                    "retention_days": 90
                }
            ]
        }
