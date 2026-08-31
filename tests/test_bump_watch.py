"""Tests for lib.bump_watch -- missed-run detection and Slack-facing bump
status. Pure functions only (fetch_recent_runs/dispatch_catchup do real
network I/O and are exercised live by tools/bump_watchdog.py, not here).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lib.bump_watch import (
    CATCHUP_THRESHOLD_HOURS,
    decide_catchup,
    format_status_fr,
    summarize_status,
)

NOW = datetime(2026, 8, 31, 11, 0, 0, tzinfo=timezone.utc)


def _run(*, hours_ago: float, status: str = "completed", conclusion: str = "success", run_id: int = 1):
    created = NOW - timedelta(hours=hours_ago)
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "created_at": created.isoformat().replace("+00:00", "Z"),
    }


# --- decide_catchup ---

def test_no_runs_at_all_skips_without_error():
    decision = decide_catchup([], now=NOW)
    assert decision["action"] == "skip"
    assert decision["reason"] == "no_completed_runs"


def test_recent_run_within_threshold_skips():
    runs = [_run(hours_ago=2)]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "skip"
    assert decision["reason"] == "within_threshold"


def test_run_exactly_at_expected_cadence_still_skips():
    """5h (the normal cadence) must not itself trigger a catch-up --
    only exceeding the threshold (cadence + safety margin) should."""
    runs = [_run(hours_ago=5.0)]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "skip"


def test_stale_run_past_threshold_triggers_dispatch():
    runs = [_run(hours_ago=CATCHUP_THRESHOLD_HOURS + 0.5, run_id=42)]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "dispatch"
    assert decision["reason"] == "missed_run"
    assert decision["latest_run_id"] == 42


def test_in_flight_run_never_triggers_a_second_dispatch():
    """Even if the last COMPLETED run is very stale, a currently
    queued/in_progress run must suppress a redundant dispatch."""
    runs = [
        _run(hours_ago=0.1, status="in_progress", conclusion=None, run_id=2),
        _run(hours_ago=20, run_id=1),
    ]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "skip"
    assert decision["reason"] == "in_flight"


def test_queued_run_also_suppresses_dispatch():
    runs = [_run(hours_ago=0.0, status="queued", conclusion=None, run_id=3)]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "skip"
    assert decision["reason"] == "in_flight"


def test_uses_the_most_recent_completed_run_not_an_older_one():
    runs = [
        _run(hours_ago=1, run_id=2),  # most recent, fresh
        _run(hours_ago=30, run_id=1),  # older, would trigger if used
    ]
    decision = decide_catchup(runs, now=NOW)
    assert decision["action"] == "skip"


# --- summarize_status ---

def test_summarize_status_with_no_runs():
    status = summarize_status([], now=NOW)
    assert status["last_run_at"] is None


def test_summarize_status_reports_last_run_and_next_expected():
    runs = [_run(hours_ago=1)]
    status = summarize_status(runs, now=NOW)
    assert status["last_conclusion"] == "success"
    assert status["last_run_at"] is not None
    assert status["next_expected_at"] is not None


def test_summarize_status_flags_in_progress():
    runs = [
        _run(hours_ago=0.0, status="in_progress", conclusion=None, run_id=2),
        _run(hours_ago=6, run_id=1),
    ]
    status = summarize_status(runs, now=NOW)
    assert status["in_progress"] is True


def test_summarize_status_lists_recent_failures():
    runs = [
        _run(hours_ago=1, conclusion="failure", run_id=3),
        _run(hours_ago=6, conclusion="success", run_id=2),
    ]
    status = summarize_status(runs, now=NOW)
    assert len(status["recent_failures"]) == 1
    assert status["recent_failures"][0]["id"] == 3


# --- format_status_fr ---

def test_format_status_fr_is_french_and_mentions_conclusion():
    runs = [_run(hours_ago=1)]
    status = summarize_status(runs, now=NOW)
    text = format_status_fr(status, now=NOW)
    assert "Dernier run" in text
    assert "success" in text


def test_format_status_fr_flags_overdue_next_run():
    runs = [_run(hours_ago=8)]  # next expected 5h after that -- already overdue
    status = summarize_status(runs, now=NOW)
    text = format_status_fr(status, now=NOW)
    assert "dépassé" in text


def test_format_status_fr_handles_no_runs_gracefully():
    status = summarize_status([], now=NOW)
    text = format_status_fr(status, now=NOW)
    assert "aucun run" in text.lower()


def test_format_status_fr_never_raises_on_realistic_data():
    runs = [_run(hours_ago=h, conclusion=c) for h, c in [(1, "success"), (6, "failure"), (11, "success")]]
    status = summarize_status(runs, now=NOW)
    text = format_status_fr(status, now=NOW)
    assert isinstance(text, str) and text
