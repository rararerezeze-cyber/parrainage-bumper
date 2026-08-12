from datetime import datetime, timezone

from lib.super_parrain_schedule import (
    decide_super_parrain_action,
    enqueue_pending,
    is_super_parrain_canary_pending,
    super_parrain_runtime_mode,
)


def test_canary_pending_blocks_bump_when_eligible(tmp_path, monkeypatch):
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
    assert d["action"] == "canary_exclusive"
    assert d["run_bump"] is False
    assert d["skip_bump"] is True
    assert d["run_canary"] is True
    assert d["canary_pending"] is True


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
    assert d["action"] == "wait"
    assert d["run_bump"] is False
    assert d["run_canary"] is False
    assert d["canary_pending"] is True
