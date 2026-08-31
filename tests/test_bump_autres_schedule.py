"""Tests for lib.bump_autres_schedule -- persisted, randomized 5-slots-
per-24h scheduling for bump_autres.yml. Pure functions + file I/O against
a monkeypatched SCHEDULE_PATH (never the real repo file). Network
functions (dispatch_workflow/fetch_last_run) are simple, well-understood
GitHub API calls exercised live by tools/bump_autres_scheduler.py, not
mocked here.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

import lib.bump_autres_schedule as sched


@pytest.fixture(autouse=True)
def _isolate_schedule_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sched, "SCHEDULE_PATH", tmp_path / "bump-autres-schedule.json")
    monkeypatch.setattr(sched, "LEDGER_PATH", tmp_path / "bump-autres-dispatch-ledger.json")


MIDNIGHT = datetime(2026, 8, 31, 0, 0, 0, tzinfo=timezone.utc)


# --- generate_slots -------------------------------------------------------

def test_exactly_five_slots_generated_at_midnight():
    slots = sched.generate_slots(date(2026, 8, 31), now=MIDNIGHT, rng=random.Random(1))
    assert len(slots) == sched.SLOTS_PER_DAY == 5
    assert [s["status"] for s in slots] == ["planned"] * 5


def test_slots_are_unique_times():
    slots = sched.generate_slots(date(2026, 8, 31), now=MIDNIGHT, rng=random.Random(1))
    times = [s["planned_at"] for s in slots]
    assert len(set(times)) == 5


def test_all_slots_fall_within_the_target_utc_day():
    day = date(2026, 8, 31)
    slots = sched.generate_slots(day, now=MIDNIGHT, rng=random.Random(1))
    start, end = sched._day_bounds(day)
    for s in slots:
        dt = datetime.fromisoformat(s["planned_at"])
        assert start <= dt < end


def test_reasonable_spread_minimum_gap_guaranteed_by_bucket_margins():
    """The bucket-margin design guarantees a minimum gap of
    2*BUCKET_MARGIN_MINUTES between any two slots (worst case: one at the
    tail of a bucket, the next at the head of the following bucket) --
    assert the actual guarantee, across many seeds, not an aspirational
    bound the mechanism doesn't actually enforce."""
    min_guaranteed_minutes = 2 * sched.BUCKET_MARGIN_MINUTES
    for seed in range(50):
        slots = sched.generate_slots(date(2026, 8, 31), now=MIDNIGHT, rng=random.Random(seed))
        times = sorted(datetime.fromisoformat(s["planned_at"]) for s in slots)
        gaps_minutes = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]
        assert all(g >= min_guaranteed_minutes - 0.01 for g in gaps_minutes), (seed, gaps_minutes)


def test_spread_is_not_a_fixed_pattern_average_gap_matches_even_division():
    """Sanity check on the mechanism itself: over many seeds, gaps should
    average out to roughly 24h/5 (~4.8h) -- confirms the buckets really do
    span the full day evenly rather than clustering in one region."""
    all_gaps = []
    for seed in range(50):
        slots = sched.generate_slots(date(2026, 8, 31), now=MIDNIGHT, rng=random.Random(seed))
        times = sorted(datetime.fromisoformat(s["planned_at"]) for s in slots)
        all_gaps.extend((b - a).total_seconds() / 3600.0 for a, b in zip(times, times[1:]))
    avg_gap = sum(all_gaps) / len(all_gaps)
    assert 4.0 <= avg_gap <= 5.6, avg_gap


def test_slots_differ_between_two_simulated_periods():
    """No fixed daily pattern: different days (or different random
    streams) must not produce the same times."""
    slots_day1 = sched.generate_slots(date(2026, 8, 31), now=MIDNIGHT, rng=random.Random(1))
    slots_day2 = sched.generate_slots(
        date(2026, 9, 1),
        now=datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc),
        rng=random.Random(2),
    )
    times1 = {s["planned_at"][11:16] for s in slots_day1}  # HH:MM only
    times2 = {s["planned_at"][11:16] for s in slots_day2}
    assert times1 != times2


def test_late_in_day_generation_only_covers_remaining_buckets():
    """Real 2026-08-31 concern: generating a fresh schedule mid-afternoon
    must not retroactively plan (and then immediately treat as 'due')
    several already-elapsed buckets -- that would flood real site bumps.
    Already-elapsed buckets get skipped_period_already_elapsed instead."""
    late_now = datetime(2026, 8, 31, 22, 0, 0, tzinfo=timezone.utc)  # 22:00 UTC
    slots = sched.generate_slots(date(2026, 8, 31), now=late_now, rng=random.Random(1))
    planned = [s for s in slots if s["status"] == "planned"]
    skipped = [s for s in slots if s["status"] == "skipped_period_already_elapsed"]
    assert len(skipped) >= 3  # buckets 0-3 (0h-19.2h) are long gone by 22:00
    assert len(planned) <= 2
    for s in planned:
        assert datetime.fromisoformat(s["planned_at"]) >= late_now


def test_generation_exactly_at_bucket_tail_never_raises():
    """Degenerate case: `now` lands inside the last few minutes of a
    bucket (window_end <= window_start after the margin) -- must still
    produce a valid immediate slot, never crash or produce a negative
    span."""
    tail_now = datetime(2026, 8, 31, 4, 47, 55, tzinfo=timezone.utc)  # near end of bucket 0 (~4.8h)
    slots = sched.generate_slots(date(2026, 8, 31), now=tail_now, rng=random.Random(1))
    for s in slots:
        if s["status"] == "planned":
            dt = datetime.fromisoformat(s["planned_at"])
            assert dt >= tail_now


# --- ensure_schedule_for: persistence / no reroll --------------------------

def test_persists_after_restart_same_period_returns_identical_slots():
    """Simulates a process restart: a second, independent call for the
    same UTC day must return byte-identical planned_at values -- never a
    reroll."""
    first = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    # New RNG instance (simulating a fresh process) -- must be irrelevant
    # once a period already has a persisted schedule.
    second = sched.ensure_schedule_for(
        MIDNIGHT + timedelta(hours=3), rng=random.Random(999)
    )
    assert first["slots"] == second["slots"]
    assert first["period_date"] == second["period_date"]


def test_new_period_after_midnight_generates_a_fresh_schedule():
    day1 = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    day2_now = datetime(2026, 9, 1, 0, 5, 0, tzinfo=timezone.utc)
    day2 = sched.ensure_schedule_for(day2_now, rng=random.Random(1))
    assert day1["period_date"] != day2["period_date"]
    assert day2["period_date"] == "2026-09-01"


def test_stale_prior_day_slots_never_leak_into_a_new_periods_due_list():
    """Midnight crossing: any slot left 'planned' from a prior day (e.g. a
    catastrophic multi-day scheduler outage) must not silently appear in
    the NEW day's due list -- a fresh ensure_schedule_for() replaces the
    whole schedule object, not merges into it."""
    sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    next_day_now = datetime(2026, 9, 1, 0, 1, 0, tzinfo=timezone.utc)
    new_schedule = sched.ensure_schedule_for(next_day_now, rng=random.Random(1))
    assert new_schedule["period_date"] == "2026-09-01"
    for s in new_schedule["slots"]:
        assert datetime.fromisoformat(s["planned_at"]) >= datetime(2026, 9, 1, tzinfo=timezone.utc)


# --- due_undispatched_slots / mark_dispatched: catch-up + no double-dispatch

def test_missed_slot_is_detected_as_due_regardless_of_how_late():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    much_later = MIDNIGHT + timedelta(hours=23)
    due = sched.due_undispatched_slots(schedule, much_later)
    # every slot whose time has passed by 23h in is due -- a slot missed
    # by hours is caught exactly like one missed by seconds.
    assert len(due) == sum(
        1 for s in schedule["slots"]
        if s["status"] == "planned" and datetime.fromisoformat(s["planned_at"]) <= much_later
    )
    assert len(due) >= 1


def test_marking_dispatched_removes_it_from_the_due_list():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    later = MIDNIGHT + timedelta(hours=23)
    due = sched.due_undispatched_slots(schedule, later)
    assert due
    slot = due[0]
    schedule = sched.mark_dispatched(schedule, slot["index"], now=later)
    due_again = sched.due_undispatched_slots(schedule, later)
    assert slot["index"] not in {s["index"] for s in due_again}


def test_double_poll_of_the_same_slot_dispatches_only_once():
    """The core anti-double-dispatch guarantee: two polls landing close
    together (or a re-run against the same persisted state) must not both
    treat the same slot as due once the first has marked it dispatched."""
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    now = MIDNIGHT + timedelta(hours=23)
    due_first_poll = sched.due_undispatched_slots(schedule, now)
    assert due_first_poll
    for slot in due_first_poll:
        schedule = sched.mark_dispatched(schedule, slot["index"], now=now)
    # Second poll, same schedule object (as if reloaded from disk), same "now"
    due_second_poll = sched.due_undispatched_slots(schedule, now)
    assert due_second_poll == []


def test_catchup_flag_set_only_when_dispatched_meaningfully_late():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    slot = schedule["slots"][0]
    planned_at = datetime.fromisoformat(slot["planned_at"])

    on_time = planned_at + timedelta(minutes=1)
    s1 = sched.mark_dispatched({"slots": [dict(slot)]}, slot["index"], now=on_time)
    assert s1["slots"][0]["catchup"] is False

    late = planned_at + timedelta(minutes=sched.CATCHUP_TOLERANCE_MINUTES + 5)
    s2 = sched.mark_dispatched({"slots": [dict(slot)]}, slot["index"], now=late)
    assert s2["slots"][0]["catchup"] is True


def test_no_due_slots_when_all_are_in_the_future():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    assert sched.due_undispatched_slots(schedule, MIDNIGHT) == []


# --- summarize / format_bump_status_fr -------------------------------------

def test_summarize_reports_planned_and_done_counts():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    now = MIDNIGHT + timedelta(hours=23)
    due = sched.due_undispatched_slots(schedule, now)
    for slot in due:
        schedule = sched.mark_dispatched(schedule, slot["index"], now=now)
    summary = sched.summarize(schedule, now=now)
    assert summary["cycles_planned"] == 5
    assert summary["cycles_done"] == len(due)


def test_summarize_next_planned_is_the_earliest_remaining_slot():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    summary = sched.summarize(schedule, now=MIDNIGHT)
    assert summary["next_planned_at"] == schedule["slots"][0]["planned_at"]


def test_format_bump_status_fr_never_claims_run_plus_five_hours():
    """The exact regression this session must not repeat: the previous
    (rejected) status text implied 'last run + 5h' cadence. The new
    format must be schedule-driven and never phrase it that way."""
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    summary = sched.summarize(schedule, now=MIDNIGHT)
    text = sched.format_bump_status_fr(summary, last_run=None)
    assert "planning aléatoire" in text or "aléatoire" in text
    assert "+5" not in text and "+ 5" not in text
    assert "cycles prévus" in text
    assert "cycles réalisés" in text


def test_format_bump_status_fr_surfaces_catchup_and_errors():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    now = MIDNIGHT + timedelta(hours=23)
    due = sched.due_undispatched_slots(schedule, now)
    for slot in due:
        schedule = sched.mark_dispatched(schedule, slot["index"], now=now)
    summary = sched.summarize(schedule, now=now)
    text = sched.format_bump_status_fr(summary, last_run={"conclusion": "failure"})
    assert "rattrapage : oui" in text or "rattrapage : non" in text
    assert "erreur" in text.lower()


# --- timezone independence (UTC only, no Europe/Paris local logic) --------

def test_everything_is_timezone_aware_utc_never_naive():
    schedule = sched.ensure_schedule_for(MIDNIGHT, rng=random.Random(1))
    for s in schedule["slots"]:
        if s["planned_at"]:
            dt = datetime.fromisoformat(s["planned_at"])
            assert dt.tzinfo is not None
            assert dt.utcoffset() == timedelta(0)


# --- exactly-once dispatch ledger (2026-08-31 crash-window fix) -----------


def test_slot_id_is_deterministic_and_derived_from_persisted_data_only():
    """No new randomness/timing: purely a function of already-persisted
    period_date + index."""
    assert sched.slot_id("2026-08-31", 2) == "2026-08-31:2"
    assert sched.slot_id("2026-08-31", 2) == sched.slot_id("2026-08-31", 2)
    assert sched.slot_id("2026-08-31", 2) != sched.slot_id("2026-08-31", 3)
    assert sched.slot_id("2026-08-31", 2) != sched.slot_id("2026-09-01", 2)


def test_slot_not_processed_by_default():
    assert sched.is_slot_already_processed("2026-08-31:2") is False


def test_empty_slot_id_is_never_considered_processed():
    """Manual/test dispatches carry no slot_id and must never be treated
    as ledger hits."""
    assert sched.is_slot_already_processed("") is False
    assert sched.is_slot_already_processed(None) is False


def test_record_then_check_round_trips():
    sid = "2026-08-31:2"
    assert sched.is_slot_already_processed(sid) is False
    sched.record_slot_processed(sid)
    assert sched.is_slot_already_processed(sid) is True


def test_recording_the_same_slot_twice_is_idempotent_no_duplicate_entries():
    sid = "2026-08-31:2"
    sched.record_slot_processed(sid)
    sched.record_slot_processed(sid)
    data = sched._load_ledger()
    assert data["dispatched_slot_ids"].count(sid) == 1


def test_ledger_persists_across_reload_simulating_a_fresh_process():
    sid = "2026-08-31:2"
    sched.record_slot_processed(sid)
    # A second, independent check (as a fresh bump_autres.yml run would do)
    # must see the same durable state.
    assert sched.is_slot_already_processed(sid) is True


def test_ledger_is_bounded_oldest_entries_pruned():
    for i in range(sched.LEDGER_MAX_ENTRIES + 10):
        sched.record_slot_processed(f"2026-01-01:{i}")
    data = sched._load_ledger()
    assert len(data["dispatched_slot_ids"]) == sched.LEDGER_MAX_ENTRIES
    # newest entries survive, oldest pruned
    assert f"2026-01-01:{sched.LEDGER_MAX_ENTRIES + 9}" in data["dispatched_slot_ids"]
    assert "2026-01-01:0" not in data["dispatched_slot_ids"]


def test_corrupted_ledger_file_degrades_to_empty_never_crashes(tmp_path):
    sched.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    sched.LEDGER_PATH.write_text("not json", encoding="utf-8")
    assert sched.is_slot_already_processed("2026-08-31:2") is False
    # And recording afterward must still work (self-heals the file).
    sched.record_slot_processed("2026-08-31:2")
    assert sched.is_slot_already_processed("2026-08-31:2") is True


def test_dispatch_workflow_includes_slot_id_as_workflow_input(monkeypatch):
    """The actual crash-window fix depends on this reaching GitHub as a
    real workflow_dispatch input -- assert the request body shape, not
    just that the function runs."""
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=30):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(sched.urllib.request, "urlopen", _fake_urlopen)
    sched.dispatch_workflow("fake-token", slot_id="2026-08-31:2")
    assert captured["body"]["inputs"] == {"slot_id": "2026-08-31:2"}


def test_dispatch_workflow_omits_inputs_when_no_slot_id(monkeypatch):
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=30):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(sched.urllib.request, "urlopen", _fake_urlopen)
    sched.dispatch_workflow("fake-token")
    assert "inputs" not in captured["body"]


def test_module_has_no_local_timezone_dependency():
    """This repo has no Europe/Paris-local time handling anywhere else;
    deliberately not introduced here (DST-transition risk for no
    demonstrated benefit) -- guard against a future accidental import of
    actual timezone-conversion machinery (the module's own docstring
    mentions "Europe/Paris" in prose explaining this decision, which is
    fine -- only real code hooks are forbidden here)."""
    import inspect

    src = inspect.getsource(sched)
    for forbidden in ("zoneinfo", "pytz", "astimezone("):
        assert forbidden not in src
