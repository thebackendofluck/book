# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""TDD test suite for runner.checks — written before implementation."""

from __future__ import annotations

import pytest


class TestRegistry:
    def test_registry_lists_four_known_checks(self) -> None:
        """checks.registry() returns the 4 GLI checks we ship: jackpot, mcs, recon, gli28."""
        from runner import checks

        names = sorted(checks.registry().keys())
        assert names == ["gli28", "jackpot", "mcs", "recon"]

    def test_each_registry_entry_has_metadata(self) -> None:
        """Every registry entry exposes name, gli_standard, schedule_hint."""
        from runner import checks

        for name, entry in checks.registry().items():
            assert entry.name == name
            assert entry.gli_standard.startswith("GLI-")
            assert entry.schedule_hint  # cron expression hint
