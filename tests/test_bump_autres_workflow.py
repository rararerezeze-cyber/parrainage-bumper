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

Also covers the same-day follow-up: the scheduler's dispatch_workflow()
(a real, durable GitHub API side effect) then mark_dispatched()+
save_schedule() then a separate commit step is not atomic -- a crash
between the dispatch succeeding and that commit landing would otherwise
let the next poll re-dispatch the same logical slot. This workflow closes
that gap itself, at the point that actually matters (before any real site
work), via a durable exactly-once ledger keyed on a deterministic
slot_id passed as a workflow_dispatch input.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_autres.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def _slice_between(start_marker: str, end_marker: str | None) -> str:
    i = TEXT.index(start_marker)
    j = TEXT.index(end_marker, i) if end_marker else len(TEXT)
    return TEXT[i:j]


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


# --- exactly-once dispatch ledger -----------------------------------------


def test_accepts_a_slot_id_input_defaulting_to_empty():
    inputs_block = _slice_between("slot_id:", "concurrency:")
    assert 'default: ""' in inputs_block
    assert "required: false" in inputs_block


def test_has_contents_write_to_commit_the_ledger():
    perms_block = _slice_between("permissions:", "steps:")
    assert "contents: write" in perms_block


def test_idempotency_check_runs_before_any_dependency_install_or_site_access():
    check_pos = TEXT.index("Exactly-once slot check")
    deps_pos = TEXT.index("📦 Dépendances")
    bump_pos = TEXT.index("🚀 Bump Code Parrainage")
    assert check_pos < deps_pos < bump_pos


def test_dependency_and_bump_steps_are_gated_on_the_idempotency_output():
    deps_step = _slice_between("📦 Dépendances", "🚀 Bump Code Parrainage")
    bump_step = _slice_between("🚀 Bump Code Parrainage", "📋 Logs")
    assert "if: steps.idempotency.outputs.should_run == 'true'" in deps_step
    assert "if: steps.idempotency.outputs.should_run == 'true'" in bump_step


def test_idempotency_check_uses_the_shared_ledger_function():
    check_step = _slice_between("Exactly-once slot check", "📦 Dépendances")
    assert "from lib.bump_autres_schedule import is_slot_already_processed" in check_step


def test_empty_slot_id_never_short_circuits_via_the_ledger():
    """A manual/test dispatch (empty slot_id) must always run -- the
    is_slot_already_processed() check itself already guards this (see
    lib.bump_autres_schedule), and the workflow computes `already` as
    `bool(slot_id) and ...` so an empty slot_id short-circuits to False
    before the ledger is even consulted."""
    check_step = _slice_between("Exactly-once slot check", "📦 Dépendances")
    assert "bool(slot_id) and is_slot_already_processed(slot_id)" in check_step


def test_ledger_record_step_only_fires_on_a_real_successful_scheduler_dispatch():
    record_step = _slice_between("Record slot as processed", "Save notification dedup state")
    condition_line = next(line for line in record_step.splitlines() if line.strip().startswith("if:"))
    assert "success()" in condition_line
    assert "steps.idempotency.outputs.should_run == 'true'" in condition_line
    assert "steps.idempotency.outputs.slot_id != ''" in condition_line


def test_ledger_record_step_uses_the_shared_record_function():
    record_step = _slice_between("Record slot as processed", "Save notification dedup state")
    assert "from lib.bump_autres_schedule import record_slot_processed" in record_step
    assert "record_slot_processed(os.environ[\"SLOT_ID\"])" in record_step


def test_ledger_commit_never_silently_swallows_a_failed_push():
    record_step = _slice_between("Record slot as processed", "Save notification dedup state")
    assert "git push || true" not in record_step
    assert "if ! git push; then" in record_step
    after_push_check = record_step.split("if ! git push; then", 1)[1]
    assert "exit 1" in after_push_check
