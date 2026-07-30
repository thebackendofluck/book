#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31b, Cache, DNS, and Traffic Surge Engineering.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Capacity model for cache, DNS, load balancer and backend-chain planning.

This script performs arithmetic only. It does not contact infrastructure.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CapacityInput:
    target_rps: float
    avg_response_bytes: float
    avg_request_bytes: float
    cache_hit_ratio: float
    public_ip_rps: float
    lb_node_rps: float
    api_pod_rps: float
    target_utilization: float
    avg_service_ms: float
    redis_ops_per_request: float
    redis_ops_per_shard: float
    db_queries_per_request: float
    db_queries_per_replica: float
    accepted_bet_rps: float
    events_per_bet: float
    kafka_events_per_partition: float


def ceil_capacity(load: float, safe_capacity: float, utilization: float) -> int:
    if load <= 0:
        return 0
    if safe_capacity <= 0:
        raise ValueError("safe capacity must be > 0")
    if utilization <= 0 or utilization > 1:
        raise ValueError("target utilization must be in (0, 1]")
    return max(1, math.ceil(load / safe_capacity / utilization))


def gbps(bytes_per_second: float) -> float:
    return bytes_per_second * 8 / 1_000_000_000


def mbps(bytes_per_second: float) -> float:
    return bytes_per_second * 8 / 1_000_000


def render(inp: CapacityInput) -> str:
    origin_rps = inp.target_rps * (1 - inp.cache_hit_ratio)
    edge_hit_rps = inp.target_rps - origin_rps
    ingress_bytes_per_sec = inp.target_rps * inp.avg_request_bytes
    egress_bytes_per_sec = inp.target_rps * inp.avg_response_bytes
    active_requests = inp.target_rps * (inp.avg_service_ms / 1000.0)
    origin_active_requests = origin_rps * (inp.avg_service_ms / 1000.0)

    public_ips = ceil_capacity(inp.target_rps, inp.public_ip_rps, inp.target_utilization)
    lb_nodes = ceil_capacity(inp.target_rps, inp.lb_node_rps, inp.target_utilization)
    api_pods = ceil_capacity(origin_rps, inp.api_pod_rps, inp.target_utilization)

    redis_ops = origin_rps * inp.redis_ops_per_request
    redis_shards = ceil_capacity(redis_ops, inp.redis_ops_per_shard, inp.target_utilization)

    db_qps = origin_rps * inp.db_queries_per_request
    db_replicas = ceil_capacity(db_qps, inp.db_queries_per_replica, inp.target_utilization)

    kafka_eps = inp.accepted_bet_rps * inp.events_per_bet
    kafka_partitions = ceil_capacity(
        kafka_eps,
        inp.kafka_events_per_partition,
        inp.target_utilization,
    )

    lines = [
        "# Capacity Model",
        "",
        "## Inputs",
        f"- target_rps: {inp.target_rps:,.0f}",
        f"- cache_hit_ratio: {inp.cache_hit_ratio:.2%}",
        f"- avg_response_bytes: {inp.avg_response_bytes:,.0f}",
        f"- avg_request_bytes: {inp.avg_request_bytes:,.0f}",
        f"- target_utilization: {inp.target_utilization:.0%}",
        "",
        "## Edge vs Origin",
        f"- edge_hit_rps: {edge_hit_rps:,.0f}",
        f"- origin_rps: {origin_rps:,.0f}",
        f"- ingress: {mbps(ingress_bytes_per_sec):,.1f} Mbps",
        f"- egress: {gbps(egress_bytes_per_sec):,.2f} Gbps",
        f"- active_requests_at_avg_latency: {active_requests:,.0f}",
        f"- origin_active_requests_at_avg_latency: {origin_active_requests:,.0f}",
        "",
        "## Front Door",
        f"- public_ips_or_vips_required: {public_ips}",
        f"- lb_nodes_required: {lb_nodes}",
        f"- api_pods_required_for_origin: {api_pods}",
        "",
        "## Backend Chain",
        f"- redis_ops_per_sec: {redis_ops:,.0f}",
        f"- redis_shards_required: {redis_shards}",
        f"- db_queries_per_sec: {db_qps:,.0f}",
        f"- db_read_replicas_or_query_slots_required: {db_replicas}",
        f"- kafka_events_per_sec: {kafka_eps:,.0f}",
        f"- kafka_partitions_required: {kafka_partitions}",
        "",
        "## Notes",
        "- Public IP count here is a capacity and blast-radius planning unit, not a hard IP protocol limit.",
        "- For inbound HTTP, one public IP can accept more than 64k total clients because client source tuples differ.",
        "- NAT, outbound fanout and single-VIP state tables can still create port and conntrack bottlenecks.",
        "- Validate every calculated capacity with stress, spike and soak tests.",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-rps", type=float, required=True)
    parser.add_argument("--avg-response-bytes", type=float, default=8192)
    parser.add_argument("--avg-request-bytes", type=float, default=1200)
    parser.add_argument("--cache-hit-ratio", type=float, default=0.95)
    parser.add_argument("--public-ip-rps", type=float, default=50_000)
    parser.add_argument("--lb-node-rps", type=float, default=10_000)
    parser.add_argument("--api-pod-rps", type=float, default=800)
    parser.add_argument("--target-utilization", type=float, default=0.60)
    parser.add_argument("--avg-service-ms", type=float, default=50)
    parser.add_argument("--redis-ops-per-request", type=float, default=2)
    parser.add_argument("--redis-ops-per-shard", type=float, default=60_000)
    parser.add_argument("--db-queries-per-request", type=float, default=0.2)
    parser.add_argument("--db-queries-per-replica", type=float, default=2_000)
    parser.add_argument("--accepted-bet-rps", type=float, default=0)
    parser.add_argument("--events-per-bet", type=float, default=8)
    parser.add_argument("--kafka-events-per-partition", type=float, default=2_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cache_hit_ratio < 0 or args.cache_hit_ratio > 1:
        raise SystemExit("--cache-hit-ratio must be between 0 and 1")

    inp = CapacityInput(
        target_rps=args.target_rps,
        avg_response_bytes=args.avg_response_bytes,
        avg_request_bytes=args.avg_request_bytes,
        cache_hit_ratio=args.cache_hit_ratio,
        public_ip_rps=args.public_ip_rps,
        lb_node_rps=args.lb_node_rps,
        api_pod_rps=args.api_pod_rps,
        target_utilization=args.target_utilization,
        avg_service_ms=args.avg_service_ms,
        redis_ops_per_request=args.redis_ops_per_request,
        redis_ops_per_shard=args.redis_ops_per_shard,
        db_queries_per_request=args.db_queries_per_request,
        db_queries_per_replica=args.db_queries_per_replica,
        accepted_bet_rps=args.accepted_bet_rps,
        events_per_bet=args.events_per_bet,
        kafka_events_per_partition=args.kafka_events_per_partition,
    )
    print(render(inp), end="")


if __name__ == "__main__":
    main()
