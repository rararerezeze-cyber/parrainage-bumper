"""A Super-Parrain execute that writes nothing must queue nothing.

Regression: tools/controlled_write_super_parrain.py used to call
enqueue_pending() unconditionally on --execute, before the plan was built and
before the cooldown and NO_SAFE_DIFF checks. Every early return then left an
open pending that nothing on that path ever closed. On a scheduled eligible slot
with no real content diff — the normal steady state now that Super-Parrain is
WRITE_VERIFIED — that produced a durable phantom pending.

No live write, no Save, no browser: execute_write is never reached in these
tests, and each one asserts that.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import super_parrain_schedule as sched
from lib.write_status import STATUS_WRITE_VERIFIED, get_platform_status

TOOL = Path(__file__).resolve().parents[1] / "tools" / "controlled_write_super_parrain.py"


def _active_pending() -> list[dict]:
    return [
        item
        for item in (sched.load_pending().get("items") or [])
        if item.get("status") == "pending"
    ]


@pytest.fixture
def no_pending():
    """Start from a clean sheet inside the session sandbox."""
    data = sched.load_pending()
    original = json.dumps(data)
    data["items"] = [i for i in (data.get("items") or []) if i.get("status") != "pending"]
    sched.save_pending(data)
    assert _active_pending() == []
    yield
    sched.PENDING_PATH.write_text(original + "\n", encoding="utf-8")


# -- the runtime guard ---------------------------------------------------------
def test_super_parrain_is_write_verified_with_normal_bump():
    """The precondition this whole guard rests on."""
    assert get_platform_status("super-parrain") == STATUS_WRITE_VERIFIED
    assert sched.super_parrain_runtime_mode() == sched.RUNTIME_MODE_NORMAL_BUMP


def test_canary_is_refused_once_verified_and_normal_bump():
    gate = sched.super_parrain_canary_allowed()
    assert gate["allowed"] is False
    assert gate["reason"] == "ALREADY_WRITE_VERIFIED_NORMAL_BUMP"
    assert "bump_super_parrain.yml" in gate["owner"]


def test_guard_would_allow_a_canary_while_still_pending(monkeypatch):
    """The guard blocks a redundant canary, not a legitimate one."""
    monkeypatch.setattr(
        sched, "super_parrain_runtime_mode", lambda: sched.RUNTIME_MODE_CANARY_PENDING
    )
    assert sched.super_parrain_canary_allowed()["allowed"] is True


# -- WRITE_VERIFIED + NORMAL_BUMP + canary => zero write, zero Save, zero pending
def test_canary_execute_is_refused_with_zero_write_and_zero_pending(no_pending, monkeypatch, capsys):
    import asyncio

    import tools.controlled_write_super_parrain as cw

    def boom(*_a, **_k):
        raise AssertionError("no write, no Save, no browser may be reached")

    monkeypatch.setattr(cw, "execute_write", boom)
    monkeypatch.setattr(asyncio, "run", boom)
    monkeypatch.setattr(
        cw.sys, "argv",
        ["controlled_write_super_parrain.py", "--program", "kraken",
         "--execute", "--force", "--canary"],
    )

    rc = cw.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "REFUSED canary" in out
    assert "ALREADY_WRITE_VERIFIED_NORMAL_BUMP" in out
    assert _active_pending() == [], "a refused canary must never queue a pending"


# -- execute + NO_SAFE_DIFF => zero pending ------------------------------------
def test_execute_with_no_safe_diff_creates_no_pending(no_pending, monkeypatch, capsys):
    import asyncio

    import tools.controlled_write_super_parrain as cw

    class _Plan:
        platform = "super-parrain"
        program = "kraken"
        language = "fr"
        structure_preserved = True
        changed_fields: dict = {}
        historical = False
        rendered = "unchanged"
        variables: dict = {}
        mutable_fields: tuple = ()

    def boom(*_a, **_k):
        raise AssertionError("NO_SAFE_DIFF must never reach a write")

    monkeypatch.setattr(cw, "build_write_plan", lambda *a, **k: _Plan())
    monkeypatch.setattr(cw, "plan_report_lines", lambda plan: ["plan: no diff"])
    monkeypatch.setattr(cw, "execute_write", boom)
    monkeypatch.setattr(asyncio, "run", boom)
    # Eligible slot: exactly the situation that used to strand a pending.
    monkeypatch.setattr(cw, "is_eligible", lambda: (True, cw.datetime.now(cw.timezone.utc), 0.0))
    monkeypatch.setattr(
        cw.sys, "argv",
        ["controlled_write_super_parrain.py", "--program", "kraken", "--execute", "--force"],
    )

    rc = cw.main()

    assert rc == 0
    assert "NO_SAFE_DIFF" in capsys.readouterr().out
    assert _active_pending() == [], "NO_SAFE_DIFF must never queue a pending"


def test_execute_without_force_creates_no_pending(no_pending, monkeypatch):
    import tools.controlled_write_super_parrain as cw

    monkeypatch.setattr(
        cw.sys, "argv",
        ["controlled_write_super_parrain.py", "--program", "kraken", "--execute"],
    )
    assert cw.main() == 2
    assert _active_pending() == []


def test_cooldown_abort_queues_only_when_a_real_diff_exists(no_pending, monkeypatch):
    """A real unapplied diff legitimately waits for the next slot; nothing else does."""
    import tools.controlled_write_super_parrain as cw

    class _Plan:
        platform = "super-parrain"
        program = "kraken"
        language = "fr"
        structure_preserved = True
        changed_fields = {"referee_reward": {"old": "a", "new": "b"}}
        historical = False
        rendered = "x"
        variables: dict = {}
        mutable_fields: tuple = ()

    later = cw.datetime.now(cw.timezone.utc)
    monkeypatch.setattr(cw, "build_write_plan", lambda *a, **k: _Plan())
    monkeypatch.setattr(cw, "plan_report_lines", lambda plan: ["plan"])
    monkeypatch.setattr(cw, "is_eligible", lambda: (False, later, 6.0))
    monkeypatch.setattr(
        cw.sys, "argv",
        ["controlled_write_super_parrain.py", "--program", "kraken", "--execute", "--force"],
    )
    assert cw.main() == 3
    assert len(_active_pending()) == 1

    # ... and with an empty diff, the same cooldown abort queues nothing.
    for item in sched.load_pending().get("items") or []:
        item["status"] = "done"
    _Plan.changed_fields = {}
    data = sched.load_pending()
    data["items"] = []
    sched.save_pending(data)
    assert cw.main() == 3
    assert _active_pending() == []


# -- the source itself must not regress ----------------------------------------
def test_every_enqueue_is_either_diff_guarded_or_past_the_no_safe_diff_check():
    """Two enqueue sites are legitimate and both are conditioned on a real diff:
    the cooldown abort (guarded by `if plan.changed_fields:`) and the real-write
    path (which the NO_SAFE_DIFF early return already protects). Any third,
    unguarded call would reintroduce the phantom pending."""
    lines = TOOL.read_text(encoding="utf-8").splitlines()
    no_safe_diff_line = next(
        i for i, l in enumerate(lines) if "if not plan.changed_fields:" in l
    )
    sites = [i for i, l in enumerate(lines) if "enqueue_pending(" in l and "import" not in l]
    assert len(sites) == 2, f"expected exactly 2 enqueue sites, found {len(sites)}"
    for i in sites:
        if i > no_safe_diff_line:
            continue  # past the NO_SAFE_DIFF early return: a real diff is certain
        window = "\n".join(lines[max(0, i - 6):i])
        assert "if plan.changed_fields:" in window, (
            f"unguarded enqueue_pending at line {i + 1}"
        )


def test_no_enqueue_happens_before_the_plan_is_built():
    """The original bug: enqueue on --execute before build_write_plan()."""
    lines = TOOL.read_text(encoding="utf-8").splitlines()
    plan_built = next(i for i, l in enumerate(lines) if "plan = build_write_plan(" in l)
    for i, l in enumerate(lines):
        if "enqueue_pending(" in l and "import" not in l:
            assert i > plan_built, "a pending must never be queued before the plan exists"
