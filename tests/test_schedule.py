from datetime import datetime, timezone

from lib.super_parrain_schedule import (
    decide_super_parrain_action,
    enqueue_pending,
    is_super_parrain_canary_pending,
    super_parrain_runtime_mode,
)


def test_canary_pending_skips_historical_even_when_eligible(tmp_path, monkeypatch):
    """Historical bumper must SKIP the first eligible slot while CANARY_PENDING."""
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"CANARY_READY"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)  # no last → eligible
    monkeypatch.setattr(ws, "STATUS_PATH", status)

    assert is_super_parrain_canary_pending() is True
    assert super_parrain_runtime_mode() == "CANARY_PENDING"
    enqueue_pending("super-parrain", "kraken", "fr")
    d = decide_super_parrain_action()
    assert d["action"] == "skip"
    assert d["run_bump"] is False
    assert d["skip_bump"] is True
    assert d["run_canary"] is False
    assert d["canary_pending"] is True
    assert d["activation_canary_owns_save"] is True
    assert d["eligible_now"] is True


def test_write_verified_allows_cycle(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"WRITE_VERIFIED"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(ws, "STATUS_PATH", status)

    assert is_super_parrain_canary_pending() is False
    d = decide_super_parrain_action()
    assert d["action"] == "cycle"
    assert d["run_bump"] is True
    assert d["skip_bump"] is False
    assert d.get("run_canary") is False
    assert d.get("activation_canary_owns_save") is False


def test_cooldown_waits_like_historical(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    last.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"CANARY_READY"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(ws, "STATUS_PATH", status)

    enqueue_pending("super-parrain", "kraken", "fr")
    d = decide_super_parrain_action()
    # Still CANARY_PENDING → always skip (cooldown or not)
    assert d["action"] == "skip"
    assert d["run_bump"] is False
    assert d["run_canary"] is False
    assert d["canary_pending"] is True
    assert d["skip_bump"] is True


def test_cycle_execute_blocked_while_canary_pending(tmp_path, monkeypatch):
    """super_parrain_cycle --execute must not launch bumper under CANARY_PENDING."""
    import sys

    import lib.super_parrain_schedule as sched
    import lib.write_status as ws
    import tools.super_parrain_cycle as cycle

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"CANARY_READY"}}}',
        encoding="utf-8",
    )
    report = tmp_path / "super-parrain-last-cycle.json"
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "CYCLE_REPORT", report)
    monkeypatch.setattr(ws, "STATUS_PATH", status)
    monkeypatch.setattr(sys, "argv", ["super_parrain_cycle.py", "--execute"])
    monkeypatch.setattr(
        cycle,
        "dry_precheck_report",
        lambda env=None: {
            "programs_scanned": 0,
            "need_update": [],
            "need_update_count": 0,
            "canary_need_update": [],
            "canary_need_update_count": 0,
            "policy": {"mode": "canary", "canary_programs": ["kraken"]},
        },
    )

    launched = {"bumper": False}

    def _no_bumper(*_a, **_k):
        launched["bumper"] = True
        raise AssertionError("bumper must not run while CANARY_PENDING")

    monkeypatch.setattr(cycle.subprocess, "run", _no_bumper)
    rc = cycle.main()
    assert rc == 3
    assert launched["bumper"] is False
    assert report.exists()
    body = report.read_text(encoding="utf-8")
    assert "BLOCKED_CANARY_PENDING" in body


def test_no_historical_save_until_slot_after_gate(tmp_path, monkeypatch):
    """Proof: from a mid-cooldown clock to next_eligible, run_bump stays False."""
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    # Slot opens 2026-08-13T05:37:10Z — freeze last_run 24h earlier
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"CANARY_READY"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(ws, "STATUS_PATH", status)

    # Before eligibility
    before = datetime(2026, 8, 13, 5, 36, 0, tzinfo=timezone.utc)
    e, nxt, _ = sched.is_eligible(before)
    assert e is False
    assert nxt.isoformat().startswith("2026-08-13T05:37:10")
    d = decide_super_parrain_action()
    assert d["run_bump"] is False
    assert d["action"] == "skip"

    # Exactly at eligibility — still skip for historical bumper
    at = datetime(2026, 8, 13, 5, 37, 11, tzinfo=timezone.utc)
    e2, _, _ = sched.is_eligible(at)
    assert e2 is True
    d2 = decide_super_parrain_action()
    assert d2["action"] == "skip"
    assert d2["run_bump"] is False
    assert d2["activation_canary_owns_save"] is True
