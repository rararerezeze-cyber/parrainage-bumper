from lib.super_parrain_schedule import (
    decide_super_parrain_action,
    enqueue_pending,
    load_pending,
)


def test_slot_opens_cycle_even_with_pending(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)

    # eligible (no last run)
    enqueue_pending("super-parrain", "kraken", "fr")
    d = decide_super_parrain_action()
    assert d["action"] == "cycle"
    assert d["run_bump"] is True
    assert d["run_precheck"] is True
    assert d.get("skip_bump") is False
    # pending must not invent a separate write-only mode
    assert d["action"] != "write"


def test_cooldown_waits_like_historical(tmp_path, monkeypatch):
    import lib.super_parrain_schedule as sched
    from datetime import datetime, timezone

    pending = tmp_path / "pending_writes.json"
    last = tmp_path / "last.txt"
    last.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", last)

    enqueue_pending("super-parrain", "kraken", "fr")
    d = decide_super_parrain_action()
    assert d["action"] == "wait"
    assert d["run_bump"] is False
