from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.slack_daily_status import build_dashboard_data, build_payload


def _state(now: datetime):
    schedule = {
        "period_date": now.date().isoformat(),
        "slots": [
            {"index": 0, "planned_at": "2026-09-15T08:00:00+00:00"},
            {"index": 1, "planned_at": "2026-09-15T17:00:00+00:00"},
            {"index": 2, "planned_at": "2026-09-15T20:00:00+00:00"},
        ],
    }
    ledger = {
        "site_completions": {
            "2026-09-15:0": ["code", "parrainage"],
            "2026-09-15:1": ["code", "parrainage"],
        }
    }
    return schedule, ledger


def test_dashboard_counts_due_successes_without_treating_future_slots_as_failures():
    now = datetime(2026, 9, 15, 18, 45, tzinfo=timezone.utc)
    schedule, ledger = _state(now)
    data = build_dashboard_data(
        now=now,
        schedule=schedule,
        ledger=ledger,
        candidates=[],
        super_info={
            "eligible": False,
            "last_at": "2026-09-14T16:00:00+00:00",
            "next_at": "2026-09-15T19:00:00+00:00",
            "jitter_minutes": 180,
        },
    )
    assert data["sites"]["code"] == {
        "done_due": 2,
        "due": 2,
        "future": 1,
        "overdue_missing": 0,
    }
    assert data["sites"]["parrainage"]["done_due"] == 2
    assert data["action_required"] is False


def test_dashboard_flags_overdue_missing_site_completion():
    now = datetime(2026, 9, 15, 18, 45, tzinfo=timezone.utc)
    schedule, ledger = _state(now)
    ledger["site_completions"]["2026-09-15:1"] = ["parrainage"]
    data = build_dashboard_data(
        now=now,
        schedule=schedule,
        ledger=ledger,
        candidates=[],
        super_info={
            "eligible": False,
            "last_at": "2026-09-14T16:00:00+00:00",
            "next_at": "2026-09-15T19:00:00+00:00",
        },
    )
    assert data["sites"]["code"]["overdue_missing"] == 1
    assert data["sites"]["parrainage"]["overdue_missing"] == 0
    assert data["action_required"] is True


def test_daily_payload_is_click_first_and_has_referralcode_shortcut():
    now = datetime(2026, 9, 15, 18, 45, tzinfo=timezone.utc)
    schedule, ledger = _state(now)
    data = build_dashboard_data(
        now=now,
        schedule=schedule,
        ledger=ledger,
        candidates=[
            {"program": "igraal", "field": "referee_reward", "canonical": "5 €", "observed": "3 €"},
            {"program": "totalenergies", "field": "referee_reward", "canonical": "20 €", "observed": "50 €"},
        ],
        super_info={
            "eligible": True,
            "last_at": "2026-09-14T16:00:00+00:00",
            "next_at": "2026-09-15T18:30:00+00:00",
        },
    )
    payload = build_payload(data, "C_TEST")
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "Code-Parrainage" in dumped
    assert "Parrainage.co" in dumped
    assert "Super-Parrain" in dumped
    assert "ReferralCode.tv" in dumped
    assert "iGraal" in dumped and "TotalEnergies" in dumped

    actions = [
        e
        for b in payload["blocks"]
        if b.get("type") == "actions"
        for e in b.get("elements", [])
    ]
    ids = [a.get("action_id") for a in actions]
    assert "autofresh_open_rctv" in ids
    assert ids.count("autofresh_command") >= 3
    manual = next(a for a in actions if a.get("action_id") == "autofresh_open_rctv")
    assert manual["url"].endswith("/my-account/?tab=listings")
    commands = [
        json.loads(a["value"])["command"]
        for a in actions
        if a.get("action_id") == "autofresh_command"
    ]
    assert "Autofresh bump" in commands
    assert "iGraal divergences" in commands
    assert "TotalEnergies divergences" in commands


def test_super_parrain_long_overdue_becomes_actionable():
    now = datetime(2026, 9, 15, 18, 45, tzinfo=timezone.utc)
    schedule, ledger = _state(now)
    data = build_dashboard_data(
        now=now,
        schedule=schedule,
        ledger=ledger,
        candidates=[],
        super_info={
            "eligible": True,
            "last_at": "2026-09-13T12:00:00+00:00",
            "next_at": "2026-09-15T10:00:00+00:00",
        },
    )
    assert data["super"]["health"] == "late"
    assert data["action_required"] is True
