"""Strict WRITE_VERIFIED semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import write_status as ws


@pytest.fixture()
def status_path(tmp_path, monkeypatch):
    p = tmp_path / "platform-write-status.json"
    monkeypatch.setattr(ws, "STATUS_PATH", p)
    # fresh defaults
    data = {
        "version": 1,
        "platforms": {k: dict(v) for k, v in ws.DEFAULT_STATUS.items()},
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_super_parrain_is_canary_ready_not_verified(status_path):
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY
    assert ws.is_write_verified("super-parrain") is False
    assert ws.is_telegram_live_capable("super-parrain") is False
    assert ws.telegram_action_for_platform("super-parrain") == "CANARY_ONLY"


def test_cannot_mark_verified_without_evidence(status_path):
    r = ws.mark_write_verified(
        "super-parrain",
        program="kraken",
        evidence={"post_match": True, "checks": {"authenticated": True}},
    )
    assert r["ok"] is False
    assert "missing_checks" in r
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY


def test_mark_verified_with_full_evidence(status_path, monkeypatch, tmp_path):
    # avoid mutating real phase file
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)

    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    r = ws.mark_write_verified(
        "super-parrain",
        program="kraken",
        evidence={
            "post_match": True,
            "checks": checks,
            "announcement_url": "https://example.test/a",
            "edit_url": "https://example.test/e",
            "public_reread": True,
            "immutable_ok": True,
        },
    )
    assert r["ok"] is True
    assert ws.is_write_verified("super-parrain")
    assert ws.is_telegram_live_capable("super-parrain")
    s = ws.summary()
    assert s["write_verified_count"] == 1
    assert "super-parrain" in s["telegram_live_capable"]
    assert s["WRITE_VERIFIED"] == "1/7"


def test_referraldrop_auth_blocked(status_path):
    assert ws.get_platform_status("referraldrop") == ws.STATUS_AUTH_BLOCKED
    assert ws.telegram_action_for_platform("referraldrop") == "AUTH_BLOCKED"


def test_count_starts_at_zero(status_path):
    s = ws.summary()
    assert s["write_verified_count"] == 0
    assert s["WRITE_VERIFIED"] == "0/7"


def test_sync_verified_no_safe_diff_is_not_write_verified(status_path):
    r = ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    assert r["ok"] is True
    assert r["write_verified"] is False
    assert ws.is_write_verified("super-parrain") is False
    assert ws.is_telegram_live_capable("super-parrain") is False
    assert ws.is_sequence_cleared("super-parrain") is True
    assert ws.get_content_sync("super-parrain") == ws.CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY


def test_compare_class_does_not_write_verify(status_path):
    r = ws.record_compare_class(
        "code-parrainage", ws.COMPARE_DOM_BLOCKED, note="slider"
    )
    assert r["write_verified"] is False
    assert ws.get_compare_class("code-parrainage") == ws.COMPARE_DOM_BLOCKED
    assert ws.is_blocked_compare("code-parrainage") is True
    assert ws.is_write_verified("code-parrainage") is False
    assert ws.is_sequence_cleared("code-parrainage") is False


def test_dom_blocked_predecessor_does_not_stall_later_real_diff(status_path):
    from lib import canary_gate as cg

    ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    ws.mark_sync_verified_no_safe_diff("parrainage-co", program="kraken")
    ws.record_compare_class("code-parrainage", ws.COMPARE_DOM_BLOCKED, note="slider")
    nxt = cg.next_executable()
    assert nxt["next"] == "1parrainage"
    gate = cg.may_execute_canary("1parrainage")
    assert gate.get("ok") is True
    assert gate.get("predecessor_skipped") == ws.COMPARE_DOM_BLOCKED
    assert ws.is_write_verified("1parrainage") is False
