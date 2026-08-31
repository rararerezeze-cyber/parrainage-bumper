"""bump_autres_watchdog.yml shape checks (plain text, same convention as
this repo's other workflow regression tests)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_autres_watchdog.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_runs_hourly_off_minute_zero():
    assert 'cron: "23 * * * *"' in TEXT


def test_has_minimal_permissions_for_dispatching_a_workflow():
    assert "actions: write" in TEXT
    assert "contents: read" in TEXT
    assert "contents: write" not in TEXT


def test_has_its_own_concurrency_group_distinct_from_the_bump_job():
    assert "group: parrainage-bumper-autres-watchdog" in TEXT
    # Must not accidentally share the bump job's own group (that would
    # make the watchdog itself queue behind/in front of bump runs instead
    # of just checking their state).
    assert "group: parrainage-bumper-autres\n" not in TEXT


def test_invokes_the_watchdog_script():
    assert "tools/bump_watchdog.py" in TEXT


def test_workflow_dispatch_available_for_manual_testing():
    assert "workflow_dispatch:" in TEXT
