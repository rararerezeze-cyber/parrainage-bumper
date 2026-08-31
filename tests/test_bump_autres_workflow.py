"""bump_autres.yml regression tests.

2026-08-31: the cron lived directly on this workflow first as "0 */5 * *
*" (GitHub's documented worst-case minute-0 congestion -- two consecutive
daily slots silently never fired), then briefly as "7 */5 * * *" (fixed
the congestion but kept a fixed daily time pattern, which was then
explicitly rejected as the wrong behavior). Timing is now owned entirely
by bump_autres_scheduler.yml's persisted, randomized 5-slots-per-day plan
(lib/bump_autres_schedule.py) -- this workflow itself must never carry a
schedule trigger again, and must never read or write the schedule state,
so a manual/test dispatch can never shift or redefine the planned slots.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_autres.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def test_has_no_schedule_trigger_of_its_own():
    assert "schedule:" not in TEXT
    assert "cron:" not in TEXT


def test_workflow_dispatch_is_the_only_trigger():
    assert "workflow_dispatch:" in TEXT


def test_never_touches_the_scheduler_state_file():
    # The state file path itself must never appear in any actual step
    # (git add / read / write) -- only bump_autres_scheduler.yml owns it.
    # (The module name lib/bump_autres_schedule.py is fine to mention in
    # an explanatory comment, which this file's header does.)
    assert "bump-autres-schedule.json" not in TEXT


def test_concurrency_group_still_prevents_overlap():
    assert "group: parrainage-bumper-autres" in TEXT
    assert "cancel-in-progress: false" in TEXT
