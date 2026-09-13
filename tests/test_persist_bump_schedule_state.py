from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from tools.persist_bump_schedule_state import merge_schedule_state


def _schedule(*, period="2026-09-13", planned="2026-09-13T05:38:21+00:00"):
    return {
        "version": 1,
        "period_date": period,
        "generated_at": f"{period}T00:04:00+00:00",
        "slots": [
            {
                "index": 0,
                "planned_at": planned,
                "status": "planned",
                "dispatched_at": None,
                "catchup": False,
            }
        ],
    }


def test_merge_promotes_dispatch_without_replacing_durable_random_time():
    durable = _schedule(planned="2026-09-13T06:00:00+00:00")
    candidate = _schedule(planned="2026-09-13T05:38:21+00:00")
    candidate["slots"][0].update(
        status="dispatched",
        dispatched_at="2026-09-13T07:34:09+00:00",
        catchup=True,
    )

    merged = merge_schedule_state(durable, candidate)

    assert merged["slots"][0]["planned_at"] == "2026-09-13T06:00:00+00:00"
    assert merged["slots"][0]["status"] == "dispatched"
    assert merged["slots"][0]["dispatched_at"] == "2026-09-13T07:34:09+00:00"
    assert merged["slots"][0]["catchup"] is True


def test_merge_keeps_earliest_duplicate_dispatch_timestamp():
    durable = _schedule()
    durable["slots"][0].update(
        status="dispatched",
        dispatched_at="2026-09-13T07:34:26+00:00",
        catchup=True,
    )
    candidate = copy.deepcopy(durable)
    candidate["slots"][0]["dispatched_at"] = "2026-09-13T07:34:08+00:00"

    merged = merge_schedule_state(durable, candidate)

    assert merged["slots"][0]["dispatched_at"] == "2026-09-13T07:34:08+00:00"


def test_merge_is_monotonic_for_recovery_metadata():
    durable = _schedule()
    durable["slots"][0].update(
        status="dispatched",
        dispatched_at="2026-09-13T05:39:00+00:00",
        recovery_count=1,
        recovery_dispatched_at="2026-09-13T05:50:00+00:00",
    )
    candidate = copy.deepcopy(durable)
    candidate["slots"][0].update(
        recovery_count=2,
        recovery_dispatched_at="2026-09-13T06:05:00+00:00",
    )

    merged = merge_schedule_state(durable, candidate)

    assert merged["slots"][0]["recovery_count"] == 2
    assert merged["slots"][0]["recovery_dispatched_at"] == "2026-09-13T06:05:00+00:00"


def test_delayed_prior_day_runner_can_never_overwrite_new_day():
    durable = _schedule(period="2026-09-14", planned="2026-09-14T05:00:00+00:00")
    candidate = _schedule(period="2026-09-13", planned="2026-09-13T05:00:00+00:00")
    candidate["slots"][0].update(
        status="dispatched",
        dispatched_at="2026-09-13T07:00:00+00:00",
    )

    assert merge_schedule_state(durable, candidate) == durable


def test_new_day_candidate_replaces_old_day_when_main_has_not_advanced_yet():
    durable = _schedule(period="2026-09-13", planned="2026-09-13T05:00:00+00:00")
    candidate = _schedule(period="2026-09-14", planned="2026-09-14T05:00:00+00:00")

    assert merge_schedule_state(durable, candidate) == candidate


def test_invalid_slot_shapes_fail_closed():
    durable = _schedule()
    candidate = _schedule()
    candidate["slots"].append(copy.deepcopy(candidate["slots"][0]))

    with pytest.raises(RuntimeError, match="slot indexes"):
        merge_schedule_state(durable, candidate)
