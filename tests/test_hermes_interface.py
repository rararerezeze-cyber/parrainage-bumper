"""Hermes → Autofresh interface contract tests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.hermes_interface import authenticate_requester, handle_request, run_autofresh_command


@pytest.fixture(autouse=True)
def _local_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", "1")
    monkeypatch.delenv("AUTOFRESH_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SHARED_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    # isolate overrides
    ov = tmp_path / "operator-overrides.json"
    ov.write_text('{"version":1,"overrides":[]}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr("lib.hermes_interface.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr(
        "lib.hermes_interface.RESULT_PATH", tmp_path / "hermes-last-result.json"
    )
    monkeypatch.setattr("lib.safety.snapshot_state", lambda *_a, **_k: {"id": "test"})


def test_auth_rejects_without_token_when_required(monkeypatch):
    monkeypatch.delenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", raising=False)
    monkeypatch.setenv("AUTOFRESH_OPERATOR_TOKEN", "secret-token")
    auth = authenticate_requester({"source": "hermes", "token": "wrong"})
    assert auth["ok"] is False


def test_auth_accepts_matching_token(monkeypatch):
    monkeypatch.delenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", raising=False)
    monkeypatch.setenv("AUTOFRESH_OPERATOR_TOKEN", "secret-token")
    auth = authenticate_requester(
        {"source": "hermes", "identity": "main", "token": "secret-token"}
    )
    assert auth["ok"] is True


def test_command_to_json_status():
    r = run_autofresh_command(
        "Kraken status",
        requester={"source": "hermes", "identity": "test"},
        persist=True,
        plan=True,
        run_writers=False,
    )
    assert r["ok"] is True
    assert r["action"] == "autofresh"
    assert r["parsed"]["program"] == "kraken"
    assert r["parsed"]["action"] == "status"
    assert "platforms" in r
    assert "write_status" in r
    assert r["monitor"] == "OBSERVATION_ONLY"
    assert "WRITE_VERIFIED" in (r.get("write_status") or {})
    assert r.get("errors") == []
    assert r.get("routing")
    assert "1parrainage" in r["routing"]["automatic_safe_diff_targets"]
    assert any(h.get("platform") == "referralcode-tv" for h in r["routing"]["human_routed_targets"])
    assert "super-parrain" in r["routing"]["blocked_targets"]
    assert "referraldrop" in r["routing"]["blocked_targets"]
    assert "parrainage-co" in r["routing"]["automatic_safe_diff_targets"]
    assert "referralcodes" in r["routing"]["blocked_targets"]


def test_global_override_and_plan_modes():
    r = run_autofresh_command(
        "Kraken gain filleul 20 €",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r["ok"] is True
    assert r["parsed"]["field"] == "referee_reward"
    assert r["result"]["new_effective"] == "20 €"
    # platform rows distinguish prepared vs verified
    modes = {p.get("write_mode") or p.get("status") for p in r.get("platforms") or []}
    assert modes  # non-empty
    # human / blocked platforms never auto-dispatch
    for p in r.get("platforms") or []:
        if p.get("platform") not in {"1parrainage", "code-parrainage", "parrainage-co"}:
            assert p.get("can_auto_write") in (False, None)
    assert r.get("routing")
    assert "1parrainage" in (r["routing"].get("automatic_safe_diff_targets") or [])
    assert "parrainage-co" in (r["routing"].get("automatic_safe_diff_targets") or [])
    human_plats = {h.get("platform") for h in r["routing"].get("human_routed_targets") or []}
    assert "referralcode-tv" in human_plats


def test_platform_override():
    r = run_autofresh_command(
        "Kraken Super-Parrain gain filleul 25 €",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r["ok"] is True
    assert r["parsed"]["platform"] == "super-parrain"
    assert "25" in (r["result"]["new_effective"] or "")


def test_structured_parse_error():
    r = run_autofresh_command(
        "????",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r["ok"] is False
    assert r["errors"]
    assert r["errors"][0]["code"] == "parse_error"


def test_handle_request_wrapper():
    body = {
        "action": "autofresh",
        "command": "Kraken status",
        "requester": {"source": "hermes", "identity": "h1"},
        "options": {"persist": True, "plan": True, "run_writers": False},
        "correlation_id": "corr-1",
    }
    r = handle_request(body)
    assert r["ok"] is True
    assert r["correlation_id"] == "corr-1"


def test_hostile_command_payload_never_crashes_or_is_mangled():
    """Downstream defense in depth for hermes_operator.yml's env-based input
    passthrough (see tests/test_hermes_operator_workflow.py): even a command
    string containing quotes, triple-quotes, newlines, $, backticks and
    unicode must reach this function completely literally and never raise
    an unhandled exception -- parsing it as a garbled command and returning
    a structured parse_error is fine, silently crashing or corrupting the
    string is not.
    """
    hostile = (
        'Kraken status\n"""not python""" \'not bash\' '
        "$HOME `id` ${{ malicious }} héllo wörld 🚀"
    )
    r = run_autofresh_command(
        hostile,
        requester={"source": "hermes", "identity": "h1'\"; DROP TABLE x;--"},
        run_writers=False,
        persist=False,
    )
    assert r["command"] == hostile
    assert isinstance(r.get("ok"), bool)
    if not r["ok"]:
        assert r["errors"]


def test_hostile_correlation_id_passed_through_unmangled():
    body = {
        "action": "autofresh",
        "command": "Kraken status",
        "requester": {"source": "hermes", "identity": "h1"},
        "options": {"persist": True, "plan": True, "run_writers": False},
        "correlation_id": "corr\n\"'$(`)`\t🚀",
    }
    r = handle_request(body)
    assert r["correlation_id"] == body["correlation_id"]


def test_idempotence_same_value():
    run_autofresh_command(
        "Kraken code IDEMP123",
        requester={"source": "hermes"},
        run_writers=False,
    )
    r2 = run_autofresh_command(
        "Kraken code IDEMP123",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r2["ok"] is True
    assert r2.get("idempotent") is True or r2.get("replayed") is True or r2["result"].get("new_effective") == "IDEMP123"
    assert r2.get("persist_confirmed") is True
