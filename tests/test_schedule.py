from datetime import datetime, timedelta, timezone

from lib.super_parrain_schedule import (
    JITTER_MAX_MINUTES,
    current_jitter_minutes,
    decide_super_parrain_action,
    enqueue_pending,
    is_super_parrain_canary_pending,
    next_eligible_at,
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


def test_write_verified_alone_still_skips_bumper(tmp_path, monkeypatch):
    """WRITE_VERIFIED is necessary but not sufficient for a historical cycle.

    Content-writer proof (WRITE_VERIFIED, e.g. the real Poulpeo canary) must
    never by itself authorize the separate global historical bumper — see
    tests/test_super_parrain_bumper_authorization.py for the full gate
    behavior. Without an explicit authorize_historical_bumper() call, the
    bumper stays SUSPENDED (fail-closed) even once WRITE_VERIFIED is true.
    """
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"WRITE_VERIFIED"}}}',
        encoding="utf-8",
    )
    phase = tmp_path / "autofresh-phase.json"  # no authorization flag set
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(ws, "STATUS_PATH", status)
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)

    assert is_super_parrain_canary_pending() is False
    assert super_parrain_runtime_mode() == "WRITE_VERIFIED_BUMPER_SUSPENDED"
    d = decide_super_parrain_action()
    assert d["action"] == "skip"
    assert d["run_bump"] is False
    assert d["skip_bump"] is True
    assert d["reason"] == "historical_bumper_not_authorized"


def test_write_verified_and_bumper_authorized_allows_cycle(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"WRITE_VERIFIED"}}}',
        encoding="utf-8",
    )
    phase = tmp_path / "autofresh-phase.json"
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(ws, "STATUS_PATH", status)
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)
    sched.authorize_historical_bumper("test operator sign-off", actor="test")

    assert is_super_parrain_canary_pending() is False
    assert super_parrain_runtime_mode() == "NORMAL_BUMP"
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
    # Pin jitter to 0 -- this test asserts the exact historical 24h mark,
    # independent of the (separately tested) 0-3h jitter feature.
    monkeypatch.setattr(sched, "_jitter_for", lambda _last: timedelta(0))

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


# --- persistent 0-3h jitter on top of the 24h cooldown ---------------------


def test_first_ever_run_has_no_jitter(tmp_path, monkeypatch):
    """No last_super_run.txt yet -> immediately eligible, no jitter delay."""
    import lib.super_parrain_schedule as sched

    monkeypatch.setattr(sched, "LAST_SUPER_RUN", tmp_path / "last.txt")
    monkeypatch.setattr(sched, "JITTER_PATH", tmp_path / "jitter.json")

    assert current_jitter_minutes() is None
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
    assert next_eligible_at(now) == now


def test_jitter_is_within_bounds_and_added_on_top_of_24h(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched

    last = tmp_path / "last.txt"
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "JITTER_PATH", tmp_path / "jitter.json")

    minutes = current_jitter_minutes()
    assert minutes is not None
    assert 0 <= minutes <= JITTER_MAX_MINUTES

    expected_base = datetime(2026, 8, 13, 5, 37, 10, 549152, tzinfo=timezone.utc)
    nxt = next_eligible_at(datetime(2026, 8, 12, 6, 0, 0, tzinfo=timezone.utc))
    assert nxt >= expected_base
    assert nxt <= expected_base + timedelta(minutes=JITTER_MAX_MINUTES)


def test_jitter_is_stable_across_repeated_polls_same_cycle(tmp_path, monkeypatch):
    """No flapping target: the same last_super_run must yield the same
    jittered next_eligible_at on every poll within the same waiting window."""
    import lib.super_parrain_schedule as sched

    last = tmp_path / "last.txt"
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "JITTER_PATH", tmp_path / "jitter.json")

    first = next_eligible_at()
    for _ in range(5):
        assert next_eligible_at() == first
    assert current_jitter_minutes() == current_jitter_minutes()


def test_jitter_persists_to_disk_for_a_fresh_process(tmp_path, monkeypatch):
    """Simulates the real GitHub Actions constraint: a fresh checkout must
    see the SAME jitter a previous run already rolled and committed, not
    re-roll a new one every poll."""
    import lib.super_parrain_schedule as sched

    last = tmp_path / "last.txt"
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    jitter_path = tmp_path / "jitter.json"
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "JITTER_PATH", jitter_path)

    first = next_eligible_at()
    assert jitter_path.exists()

    # "fresh process": re-read from the persisted file only, no in-memory state.
    second = next_eligible_at()
    assert second == first


def test_jitter_rerolls_only_after_a_new_real_cycle(tmp_path, monkeypatch):
    """A genuinely new last_super_run value (a real success) gets its own,
    independently-rolled jitter -- the old cycle's jitter must not leak."""
    import lib.super_parrain_schedule as sched

    last = tmp_path / "last.txt"
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "JITTER_PATH", tmp_path / "jitter.json")

    first_next = next_eligible_at()

    # A new real cycle succeeds -- last_super_run.txt advances.
    last.write_text("2026-08-14T09:00:00+00:00", encoding="utf-8")
    second_next = next_eligible_at()
    assert second_next != first_next
    assert second_next >= datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def test_decide_action_exposes_jitter_minutes(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched
    import lib.write_status as ws

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    last.write_text("2026-08-12T05:37:10.549152+00:00", encoding="utf-8")
    status = tmp_path / "platform-write-status.json"
    status.write_text(
        '{"version":1,"platforms":{"super-parrain":{"status":"CANARY_READY"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)
    monkeypatch.setattr(sched, "JITTER_PATH", tmp_path / "jitter.json")
    monkeypatch.setattr(ws, "STATUS_PATH", status)

    d = decide_super_parrain_action()
    assert d["jitter_minutes"] is not None
    assert 0 <= d["jitter_minutes"] <= JITTER_MAX_MINUTES
