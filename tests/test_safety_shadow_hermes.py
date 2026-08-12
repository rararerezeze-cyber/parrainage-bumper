"""Safety / SHADOW / Hermes persist+serialize — no live network."""
from __future__ import annotations

import json

from lib.hermes_interface import handle_request, run_autofresh_command
from lib.monitor.models import Confidence, FieldChange, Observation, ObservationStatus
from lib.monitor.shadow import SHADOW_ACCEPT, SHADOW_REJECT, SHADOW_REVIEW, decide_candidate, run_shadow
from lib.safety import classify_stop, is_circuit_open, rollback_snapshot, snapshot_state, trip_circuit


def test_classify_stop_kinds():
    assert classify_stop("HTTP 429 too many requests", status_code=429) == "429"
    assert classify_stop("forbidden", status_code=403) == "403"
    assert classify_stop("Please complete the CAPTCHA") == "CAPTCHA"
    assert classify_stop("login echoue") == "auth"


def test_circuit_and_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.safety.DATA_DIR", tmp_path)
    monkeypatch.setattr("lib.safety.SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr("lib.safety.AUDIT_PATH", tmp_path / "audit" / "events.jsonl")
    monkeypatch.setattr("lib.safety.CIRCUIT_PATH", tmp_path / "circuit-breakers.json")
    (tmp_path / "operator-overrides.json").write_text('{"overrides":[]}\n', encoding="utf-8")
    trip_circuit("test", platform="1parrainage", kind="429")
    open_, reason = is_circuit_open("1parrainage")
    assert open_ is True
    assert "429" in (reason or "") or "test" in (reason or "")
    snap = snapshot_state("unit")
    assert snap["id"]
    (tmp_path / "operator-overrides.json").write_text('{"overrides":[{"x":1}]}\n', encoding="utf-8")
    rb = rollback_snapshot(snap["id"])
    assert rb["ok"] is True
    data = json.loads((tmp_path / "operator-overrides.json").read_text(encoding="utf-8"))
    assert data.get("overrides") == []


def test_shadow_never_auto_accepts():
    cand = {
        "program": "generic-test",
        "field": "referee_reward",
        "canonical": "10 €",
        "observed": "20 €",
        "valid_authority": True,
        "source_country": "FR",
        "live_high_streak": 3,
        "announcement_impact": 5,
        "authority": "OFFICIAL_PUBLIC_MONITOR",
    }
    d = decide_candidate(cand, circuits={"global_open": False})
    assert d["decision"] == SHADOW_ACCEPT
    assert d["auto_applied"] is False
    assert d["would_write"] is False

    personal = dict(cand, field="personal_code")
    assert decide_candidate(personal, circuits={"global_open": False})["decision"] == SHADOW_REJECT

    low = dict(cand, live_high_streak=1)
    assert decide_candidate(low, circuits={"global_open": False})["decision"] == SHADOW_REVIEW

    obs = [
        Observation(
            program="kraken",
            status=ObservationStatus.CANDIDATE,
            confidence=Confidence.HIGH,
            source_url="https://example.test",
            parser="x",
            detected_at="t",
            canonical_fields={"referee_reward": "10 €"},
            observed_fields={"referee_reward": "20 €"},
            changes=[FieldChange(field="referee_reward", old="10 €", new="20 €")],
            field_authority={"referee_reward": "OFFICIAL_PUBLIC_MONITOR"},
            live_high_streak=3,
            impact_count=5,
            source_country="FR",
        )
    ]
    report = run_shadow(obs, persist=False)
    assert report["auto_accept"] is False
    assert report["auto_write"] is False
    assert report["MONITOR_SHADOW_READY"] == "YES"


def test_hermes_persist_confirmed_before_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", "1")
    monkeypatch.delenv("AUTOFRESH_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SHARED_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr("lib.safety.snapshot_state", lambda *_a, **_k: {"id": "test"})
    ov = tmp_path / "operator-overrides.json"
    ov.write_text('{"version":1,"overrides":[]}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr("lib.hermes_interface.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr("lib.hermes_interface.RESULT_PATH", tmp_path / "hermes-last-result.json")
    r = run_autofresh_command(
        "Kraken code PERSIST1",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r["ok"] is True
    assert r.get("persist_confirmed") is True
    disk = json.loads(ov.read_text(encoding="utf-8"))
    assert any(o.get("value") == "PERSIST1" for o in disk.get("overrides") or [])

    r2 = run_autofresh_command(
        "Kraken code PERSIST1",
        requester={"source": "hermes"},
        run_writers=False,
    )
    assert r2["ok"] is True
    assert r2.get("idempotent") is True or r2.get("replayed") is True


def test_oneparrainage_dry_plan_structure():
    from platforms.oneparrainage.writer import build_write_plan, dry_run_report

    plan = build_write_plan("1parrainage", "kraken", "fr")
    assert plan.structure_preserved is True
    assert plan.platform_offer_id == "100408"
    assert plan.announcement_url
    report = dry_run_report("kraken")
    assert report["live"] is False
    assert report["pipeline"][:3] == ["login", "edit", "save"]
