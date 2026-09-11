from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _triggers(name: str):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data.get(True) or data.get("on") or {}


def test_release_is_production_with_known_limitations():
    release = (ROOT / "docs" / "RELEASE-STATE.md").read_text(encoding="utf-8")
    phase = json.loads((ROOT / "data" / "autofresh-phase.json").read_text(encoding="utf-8"))
    assert "AUTOFRESH_RELEASE_STATUS = FINISHED_WITH_KNOWN_LIMITATIONS" in release
    assert phase["phase"] == "PRODUCTION"
    assert phase["monitor_auto_accept"] is False


def test_production_workflows_use_production_phase():
    for name in ("hermes_operator.yml", "bump_super_parrain.yml", "monitor_offers.yml"):
        raw = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert 'AUTOFRESH_PHASE: "PRODUCTION"' in raw
        assert 'AUTOFRESH_PHASE: "VALIDATION_LIVE"' not in raw


def test_randomized_scheduler_has_single_external_wakeup_owner():
    scheduler = _triggers("bump_autres_scheduler.yml")
    assert "schedule" not in scheduler
    assert "workflow_dispatch" in scheduler

    wrangler = (ROOT / "slack-worker" / "wrangler.toml").read_text(encoding="utf-8")
    assert 'crons = ["3,18,33,48 * * * *"]' in wrangler

    registry = json.loads((ROOT / "data" / "workflow-registry.json").read_text(encoding="utf-8"))
    row = registry["workflows"]["bump_autres_scheduler.yml"]
    assert row["class"] == "PRODUCTION_EXTERNAL_TRIGGER"


def test_referralcode_tv_is_manual_only_while_turnstile_gate_is_known():
    triggers = _triggers("bump_referralcode_tv.yml")
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers

    registry = json.loads((ROOT / "data" / "workflow-registry.json").read_text(encoding="utf-8"))
    assert registry["workflows"]["bump_referralcode_tv.yml"]["class"] == "PRODUCTION_MANUAL"

    status = json.loads((ROOT / "data" / "platform-write-status.json").read_text(encoding="utf-8"))
    rctv = status["platforms"]["referralcode-tv"]
    assert rctv["scheduled_bump_autonomy"] == "MANUAL_ONLY_EXTERNAL_GATE"


def test_current_docs_do_not_restore_removed_crons_or_old_rctv_mode():
    current_docs = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "docs/RELEASE-STATE.md",
            "docs/HANDOFF_CODEX.md",
            "docs/autofresh-slack-interface.md",
            "slack-worker/README.md",
        )
    )
    assert "BUMP_ISOLATED_BEST_EFFORT" not in current_docs
    assert "GitHub's native scheduler remains enabled" not in current_docs
    assert "GitHub's native cron is kept as a backup" not in current_docs
