"""Every workflow is classified, and the closed ones stay closed.

data/workflow-registry.json is the operator-facing map of what actually runs
the product versus what is kept only as historical proof. This test fails if a
workflow is added, removed, or silently reclassified.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
REGISTRY = json.loads((ROOT / "data" / "workflow-registry.json").read_text(encoding="utf-8"))

SCHEDULED = {
    "bump_super_parrain.yml",
    "bump_autres.yml",
    "bump_referralcode_tv.yml",
    "monitor_offers.yml",
    # Also cron-driven: the daily Super-Parrain activation canary. It shares
    # bump_super_parrain.yml's concurrency group on purpose (one live saver).
    "activation_canary.yml",
}


def _workflow_files() -> set[str]:
    return {p.name for p in WORKFLOW_DIR.glob("*.yml")}


def test_every_workflow_is_classified():
    assert _workflow_files() == set(REGISTRY["workflows"])


def test_every_class_is_documented():
    known = set(REGISTRY["classes"])
    for name, meta in REGISTRY["workflows"].items():
        assert meta["class"] in known, name


@pytest.mark.parametrize("name", sorted(_workflow_files()))
def test_workflow_yaml_is_valid(name):
    data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("jobs"), name


def test_only_the_registered_workflows_run_on_a_schedule():
    """A new cron must be a deliberate, classified decision."""
    scheduled = set()
    for name in _workflow_files():
        data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        triggers = data.get(True) or data.get("on") or {}
        if isinstance(triggers, dict) and triggers.get("schedule"):
            scheduled.add(name)
    assert scheduled == SCHEDULED


def test_scheduled_workflows_are_production():
    for name in SCHEDULED:
        assert REGISTRY["workflows"][name]["class"] == "PRODUCTION_SCHEDULED", name


def test_closed_workflows_are_never_scheduled():
    """An archived proof must not be able to fire itself."""
    closed = {
        name
        for name, meta in REGISTRY["workflows"].items()
        if meta["class"] in {"EVIDENCE_CLOSED", "CANARY_CLOSED", "DIAGNOSTIC_CLOSED"}
    }
    assert closed and not (closed & SCHEDULED)


def test_the_three_bumpers_never_share_a_concurrency_group():
    groups = {}
    for name in ("bump_super_parrain.yml", "bump_autres.yml", "bump_referralcode_tv.yml"):
        data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        groups[name] = (data.get("concurrency") or {}).get("group")
    assert len(set(groups.values())) == 3, groups
    assert all(groups.values()), groups


def test_scheduled_bumpers_do_not_all_start_at_the_same_minute():
    crons = {}
    for name in ("bump_autres.yml", "bump_referralcode_tv.yml"):
        data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        triggers = data.get(True) or data.get("on") or {}
        crons[name] = [s["cron"] for s in triggers["schedule"]]
    assert crons["bump_autres.yml"] != crons["bump_referralcode_tv.yml"]


# -- the closed gates are closed at runtime, not only on paper -----------------
def test_write_verified_platforms_refuse_a_new_canary():
    from lib.canary_gate import may_execute_canary

    for platform in ("parrainage-co", "code-parrainage", "1parrainage", "referralcode-tv"):
        gate = may_execute_canary(platform)
        assert gate["ok"] is False, platform
        assert gate["error"] == "already_WRITE_VERIFIED", platform


def test_the_1parrainage_evidence_probe_is_permanently_closed():
    from lib.canary_gate import guard_live_evidence_probe

    gate = guard_live_evidence_probe(
        "1parrainage", evidence_field="gh_headless_save", expected_value="NOT_RUN"
    )
    assert gate["ok"] is False
    assert gate.get("done") is True, "the proof is complete; a re-run must be refused"


def test_referralcodes_can_never_auto_commit():
    from platforms.referralcodes.writer import execute_write

    result = execute_write("kraken", dry_run=False)
    assert result["ok"] is False
    assert "NEVER_AUTO_COMMIT" in result["error"]
    assert "committed" not in (result.get("steps") or [])


def test_monitor_auto_accept_stays_disabled():
    from lib.monitor.auto_accept import auto_accept_enabled

    assert auto_accept_enabled() is False


# -- observability reaches Hermes ----------------------------------------------
NOTIFY_UPLOADING = {
    "bump_autres.yml",
    "bump_super_parrain.yml",
    "bump_referralcode_tv.yml",
    "monitor_offers.yml",
    "hermes_operator.yml",
}


@pytest.mark.parametrize("name", sorted(NOTIFY_UPLOADING))
def test_production_workflows_upload_the_notification_outbox(name):
    """data/notifications/ is gitignored, so the artifact is its only way out."""
    data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    steps = [s for job in data["jobs"].values() for s in (job.get("steps") or [])]
    uploads = [
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
        and "data/notifications/" in str((s.get("with") or {}).get("path", ""))
    ]
    assert uploads, f"{name} never exports its events"
    assert uploads[0].get("if") == "always()", f"{name} must export events even on failure"


def test_notification_upload_never_fails_a_run_when_there_is_nothing_to_upload():
    for name in NOTIFY_UPLOADING:
        data = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        steps = [s for job in data["jobs"].values() for s in (job.get("steps") or [])]
        for s in steps:
            with_ = s.get("with") or {}
            if "data/notifications/" in str(with_.get("path", "")):
                assert with_.get("if-no-files-found") == "ignore", name
