from lib.super_parrain_schedule import (
    decide_super_parrain_action,
    enqueue_pending,
    is_eligible,
    load_pending,
    save_pending,
)


def test_enqueue_and_decide_prefers_write_or_wait(tmp_path, monkeypatch):
    # isolate pending file
    import lib.super_parrain_schedule as sched

    pending = tmp_path / "pending_writes.json"
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    monkeypatch.setattr(sched, "LAST_SUPER_RUN", tmp_path / "last.txt")

    # No last run → eligible
    item = enqueue_pending("super-parrain", "kraken", "fr")
    assert item["status"] == "pending"
    d = decide_super_parrain_action()
    assert d["action"] in {"write", "wait", "bump"}
    assert d["skip_bump"] is True or d["action"] == "write"

    # With pending, never pure bump
    assert d["action"] != "bump" or not load_pending().get("items")
