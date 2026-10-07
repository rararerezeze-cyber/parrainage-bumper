from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.slack_daily_status import build_dashboard_data, build_payload, monitor_health


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
        monitor_info={"health": "healthy"},
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

    for block in payload["blocks"]:
        if block.get("type") != "actions":
            continue
        block_ids = [e.get("action_id") for e in block.get("elements", [])]
        assert len(block_ids) == len(set(block_ids))


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


def _monitor_fixture():
    now = datetime(2026, 10, 7, 18, 45, tzinfo=timezone.utc)
    report = {"generated_at": "2026-10-07T08:20:00Z", "run_id": "42", "by_status": {"ERROR": 0}}
    batch = {"run_id": "42", "ok": True, "failed_writes": 0}
    run = {"id": 42, "created_at": "2026-10-07T08:17:00Z", "status": "completed", "conclusion": "success"}
    return now, report, batch, run


def test_fresh_successful_monitor_can_report_no_pending_changes():
    now, report, batch, run = _monitor_fixture()
    health = monitor_health(now, report, batch, run)
    assert health["health"] == "healthy"
    payload = build_payload({"generated_at": now.isoformat(), "monitor": health}, "C_TEST")
    assert "aucun changement confirmé en attente" in json.dumps(payload, ensure_ascii=False)


def test_failed_writers_and_failed_workflow_never_show_stale_no_changes():
    now, report, batch, run = _monitor_fixture()
    batch.update(ok=False, failed_writes=6)
    run["conclusion"] = "failure"
    health = monitor_health(now, report, batch, run)
    text = json.dumps(build_payload({"monitor": health}, "C_TEST"), ensure_ascii=False)
    assert "failure" in text
    assert "6 écriture(s)" in text
    assert "aucun changement confirmé en attente" not in text
    assert "Rien d'urgent" not in text


def test_fresh_workflow_does_not_make_old_observations_healthy():
    now, report, batch, run = _monitor_fixture()
    report["generated_at"] = "2026-09-21T08:20:00Z"
    report["run_id"] = "old"
    health = monitor_health(now, report, batch, run)
    assert health["health"] == "degraded"
    assert any("périmées" in reason for reason in health["reasons"])
    assert any("non persistées" in reason for reason in health["reasons"])


def test_missing_health_api_or_batch_fails_closed():
    now, report, batch, run = _monitor_fixture()
    for missing in [monitor_health(now, report, batch, None), monitor_health(now, report, {}, run)]:
        assert missing["health"] == "degraded"
    assert monitor_health(now, {}, {}, None)["health"] == "degraded"
