# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""TDD: Prometheus metrics emission after a check runs."""

from __future__ import annotations

from runner.checks import CheckResult


def test_record_emits_counter_and_duration() -> None:
    from runner import metrics

    reg = metrics.fresh_registry()
    metrics.record(
        reg,
        CheckResult(
            name="jackpot",
            success=True,
            return_code=0,
            duration_s=0.42,
            stdout="OK",
            stderr="",
            timed_out=False,
        ),
    )

    exposition = metrics.render(reg)
    assert 'gli_check_runs_total{check="jackpot",outcome="success"}' in exposition
    assert "gli_check_duration_seconds" in exposition
    assert "jackpot" in exposition


def test_record_failure_separates_outcome_label() -> None:
    from runner import metrics

    reg = metrics.fresh_registry()
    metrics.record(
        reg,
        CheckResult(
            name="mcs",
            success=False,
            return_code=2,
            duration_s=1.2,
            stdout="",
            stderr="drift",
            timed_out=False,
        ),
    )

    exposition = metrics.render(reg)
    assert 'gli_check_runs_total{check="mcs",outcome="failure"}' in exposition


def test_record_timeout_uses_timeout_outcome() -> None:
    from runner import metrics

    reg = metrics.fresh_registry()
    metrics.record(
        reg,
        CheckResult(
            name="recon",
            success=False,
            return_code=-1,
            duration_s=10.0,
            stdout="",
            stderr="",
            timed_out=True,
        ),
    )

    exposition = metrics.render(reg)
    assert 'gli_check_runs_total{check="recon",outcome="timeout"}' in exposition
