#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Calculate a 0-100 security score from multiple data sources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityInputs:
    critical_findings: int
    high_findings: int
    sla_breaches: int
    runtime_alerts_24h: int


def calculate_score(inputs: SecurityInputs) -> int:
    penalty = (
        inputs.critical_findings * 20
        + inputs.high_findings * 8
        + inputs.sla_breaches * 12
        + inputs.runtime_alerts_24h * 2
    )
    return max(0, min(100, 100 - penalty))


if __name__ == "__main__":
    sample = SecurityInputs(critical_findings=0, high_findings=2, sla_breaches=0, runtime_alerts_24h=3)
    print(calculate_score(sample))
