"""bump_autres_scheduler.yml shape checks (plain text, same convention as
this repo's other workflow regression tests)."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_autres_scheduler.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_has_no_native_schedule_to_avoid_duplicate_cloudflare_polls():
    data = yaml.safe_load(TEXT)
    triggers = data.get(True) or data.get("on") or {}
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers


def test_has_permissions_to_dispatch_and_commit():
    assert "actions: write" in TEXT
    assert "contents: write" in TEXT


def test_has_its_own_concurrency_group():
    assert "group: parrainage-bumper-autres-scheduler" in TEXT


def test_invokes_the_scheduler_script():
    assert "tools/bump_autres_scheduler.py" in TEXT


def test_commits_the_schedule_state_with_a_real_push_failure_gate():
    assert "data/bump-autres-schedule.json" in TEXT
    assert "if ! git push; then" in TEXT
    assert "exit 1" in TEXT


def test_workflow_dispatch_available_for_manual_testing():
    assert "workflow_dispatch:" in TEXT
