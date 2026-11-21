"""Minimal tests for the regulator_notify deadlines table (Ch. 35b matrix)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import regulator_notify as rn  # noqa: E402  # ty: ignore[unresolved-import]

# Friday 2026-04-24 10:00 UTC
FRIDAY = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)


def test_matrix_values():
    assert rn.JURISDICTIONS["UKGC"]["sla_value"] == 5
    assert rn.JURISDICTIONS["UKGC"]["sla_unit"] == "working_days"
    assert rn.JURISDICTIONS["UKGC"]["ref"] == "LCCP 15.2.1"
    assert rn.JURISDICTIONS["MGA"]["sla_value"] == 3
    assert rn.JURISDICTIONS["MGA"]["ref"] == (
        "Player Protection Directive, Article 37(2)(c)-(d)")


def test_no_invented_24h_default():
    for key, j in rn.JURISDICTIONS.items():
        assert "sla_hours" not in j
        if key not in ("UKGC", "MGA"):
            assert j["sla_unit"] is None and j["sla_value"] is None
            assert j["sla_text"] == rn.UNKNOWN_DEADLINE


def test_working_days_skip_weekends():
    assert rn.compute_deadline("UKGC", FRIDAY).date().isoformat() == "2026-05-01"
    assert rn.compute_deadline("MGA", FRIDAY).date().isoformat() == "2026-04-29"


@pytest.mark.parametrize("key", ["DGE", "AGCO", "Spelinspektionen", "SIGAP",
                                 "Curacao", "Gibraltar"])
def test_unknown_deadline_fails_loudly(key):
    with pytest.raises(rn.UnknownDeadlineError):
        rn.compute_deadline(key, FRIDAY)


def test_confirmed_hours_accepted_for_unknown():
    d = rn.compute_deadline("DGE", FRIDAY, confirmed_hours=48)
    assert d.date().isoformat() == "2026-04-26"


def test_render_unknown_raises_and_known_renders():
    base = dict(incident_id="INC-1", detected_at="2026-04-24T10:00:00Z",
                severity="SEV-2", house_loss_eur=1000.0, rounds_affected=10,
                action_taken="x", root_cause_known=False)
    with pytest.raises(rn.UnknownDeadlineError):
        rn.render(SimpleNamespace(jurisdiction="SIGAP", **base))
    text = rn.render(SimpleNamespace(jurisdiction="UKGC", **base))
    assert "LCCP 15.2.1" in text and "SEV-2" in text and "24h" not in text
