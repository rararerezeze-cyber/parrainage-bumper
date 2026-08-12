"""Sequential canary gates — no live write, no network."""
from __future__ import annotations

import json

from lib.canary_gate import (
    POST_SUPER_EXECUTABLE,
    may_execute_canary,
    next_executable,
    predecessor,
)
from lib import write_status as ws


def test_predecessor_chain():
    assert predecessor("super-parrain") is None
    assert predecessor("parrainage-co") == "super-parrain"
    assert predecessor("code-parrainage") == "parrainage-co"
    assert predecessor("1parrainage") == "code-parrainage"
    assert predecessor("referralcodes") == "1parrainage"
    assert predecessor("referralcode-tv") == "referralcodes"


def test_refuses_before_super_pass(tmp_path, monkeypatch):
    p = tmp_path / "platform-write-status.json"
    data = {
        "version": 1,
        "platforms": {k: dict(v) for k, v in ws.DEFAULT_STATUS.items()},
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ws, "STATUS_PATH", p)
    monkeypatch.setattr("lib.canary_gate.is_write_verified", lambda plat: False)
    monkeypatch.setattr("lib.canary_gate.get_platform_status", ws.get_platform_status)
    monkeypatch.setattr("lib.canary_gate.is_circuit_open", lambda *_a, **_k: (False, None))

    g = may_execute_canary("parrainage-co")
    assert g["ok"] is False
    assert g["error"] == "SUPER_PASS_REQUIRED"
    nxt = next_executable(for_super=False)
    assert nxt["next"] is None


def test_one_at_a_time_after_super(tmp_path, monkeypatch):
    verified = {"super-parrain"}

    def _verified(plat):
        return plat in verified

    def _status(plat):
        return ws.STATUS_WRITE_VERIFIED if plat in verified else ws.STATUS_CANARY_READY

    monkeypatch.setattr("lib.canary_gate.is_write_verified", _verified)
    monkeypatch.setattr("lib.canary_gate.get_platform_status", _status)
    monkeypatch.setattr("lib.canary_gate.is_circuit_open", lambda *_a, **_k: (False, None))

    assert may_execute_canary("parrainage-co")["ok"] is True
    assert may_execute_canary("code-parrainage")["ok"] is False
    assert "PREDECESSOR_NOT_PASS" in may_execute_canary("code-parrainage")["error"]
    assert may_execute_canary("1parrainage")["ok"] is False
    assert may_execute_canary("referralcodes")["ok"] is False
    assert next_executable()["next"] == "parrainage-co"

    verified.add("parrainage-co")
    assert next_executable()["next"] == "code-parrainage"
    assert may_execute_canary("code-parrainage")["ok"] is True
    assert may_execute_canary("1parrainage")["ok"] is False

    verified.add("code-parrainage")
    assert next_executable()["next"] == "1parrainage"
    verified.add("1parrainage")
    assert next_executable()["next"] == "referralcodes"


def test_post_super_set():
    assert POST_SUPER_EXECUTABLE == (
        "parrainage-co",
        "code-parrainage",
        "1parrainage",
        "referralcodes",
    )
