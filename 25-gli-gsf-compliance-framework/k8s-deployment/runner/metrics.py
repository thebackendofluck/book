# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Prometheus metrics for GLI compliance check runs."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from runner.checks import CheckResult


def fresh_registry() -> CollectorRegistry:
    """A new registry — used per-test and per-process to avoid double-registration."""
    return CollectorRegistry()


def _runs_counter(reg: CollectorRegistry) -> Counter:
    return Counter(
        "gli_check_runs_total",
        "Total GLI compliance check executions, labelled by check and outcome.",
        labelnames=("check", "outcome"),
        registry=reg,
    )


def _duration_histogram(reg: CollectorRegistry) -> Histogram:
    return Histogram(
        "gli_check_duration_seconds",
        "Wall-clock duration of GLI compliance check executions.",
        labelnames=("check",),
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0),
        registry=reg,
    )


def record(reg: CollectorRegistry, result: CheckResult) -> None:
    runs = _existing_or_new(reg, "gli_check_runs_total", _runs_counter)
    duration = _existing_or_new(reg, "gli_check_duration_seconds", _duration_histogram)

    if result.timed_out:
        outcome = "timeout"
    elif result.success:
        outcome = "success"
    else:
        outcome = "failure"

    runs.labels(check=result.name, outcome=outcome).inc()
    duration.labels(check=result.name).observe(result.duration_s)


def render(reg: CollectorRegistry) -> str:
    return generate_latest(reg).decode("utf-8")


def preregister(reg: CollectorRegistry) -> None:
    _existing_or_new(reg, "gli_check_runs_total", _runs_counter)
    _existing_or_new(reg, "gli_check_duration_seconds", _duration_histogram)


def _existing_or_new(reg: CollectorRegistry, fq_name: str, factory):
    for collector, names in reg._collector_to_names.items():  # type: ignore[attr-defined]
        if fq_name in names or any(n.startswith(fq_name) for n in names):
            return collector
    return factory(reg)
