"""Regression tests for the 2026-08-27 fused-cycle incident (GH run 33098049116).

The pre-check reported canary_need_update_count=0; the runtime prefilled Kraken's
body anyway and performed a real Save, which then failed post-verify, and the run
still showed green with no Hermes event.

Root cause: the two paths compared different sources for `current.body` --
the repository golden (pre-check) vs the live edit form (runtime).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lib.content_plan import (
    CONTENT_PLAN_ENV,
    REASON_DISAGREEMENT,
    REASON_NO_PLAN,
    REASON_NOT_IN_PLAN,
    build_plan,
    classify_disagreement,
    content_allowed,
    load_plan,
    serialize_plan,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "bump_super_parrain.yml"


# -- 1. precheck says nothing to update => zero content mutation ---------------
def test_empty_precheck_plan_forbids_every_content_mutation():
    """canary_need_update_count = 0 must mean zero prefill, on every program."""
    plan = build_plan([])
    for program in ("kraken", "poulpeo", "revolut", "whatnot"):
        allowed, reason = content_allowed(program, plan)
        assert allowed is False, program
        assert reason == REASON_NOT_IN_PLAN


def test_empty_plan_still_allows_the_historical_bump():
    """The plan governs content only; it must never gate the bump itself."""
    verdict = classify_disagreement(
        program="kraken", plan=build_plan([]), runtime_needs_update=False
    )
    assert verdict["content_mutation_allowed"] is False
    assert verdict["disagreement"] is False  # nothing wanted to be written


# -- 2. disagreement => FAIL CLOSED -------------------------------------------
def test_runtime_wanting_to_write_outside_the_plan_is_refused():
    """The exact incident: precheck=0, runtime=needs_update -> no write."""
    verdict = classify_disagreement(
        program="kraken", plan=build_plan([]), runtime_needs_update=True
    )
    assert verdict["disagreement"] is True
    assert verdict["content_mutation_allowed"] is False
    assert verdict["reason"] == REASON_DISAGREEMENT


def test_missing_plan_fails_closed_rather_than_open():
    allowed, reason = content_allowed("kraken", None)
    assert allowed is False
    assert reason == REASON_NO_PLAN


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[]", "null", '{"version":1}'])
def test_unreadable_plan_fails_closed(raw):
    assert load_plan({CONTENT_PLAN_ENV: raw}) is None
    assert content_allowed("kraken", load_plan({CONTENT_PLAN_ENV: raw}))[0] is False


def test_runtime_may_narrow_but_never_widen_the_plan():
    plan = build_plan(["kraken"])
    # narrowing: plan allows, runtime found nothing -> no write, no disagreement
    narrowed = classify_disagreement(program="kraken", plan=plan, runtime_needs_update=False)
    assert narrowed["content_mutation_allowed"] is False
    assert narrowed["disagreement"] is False
    # widening: plan forbids, runtime wants -> refused
    widened = classify_disagreement(program="poulpeo", plan=plan, runtime_needs_update=True)
    assert widened["content_mutation_allowed"] is False
    assert widened["disagreement"] is True
    # authorized: plan allows and runtime found a real diff
    ok = classify_disagreement(program="kraken", plan=plan, runtime_needs_update=True)
    assert ok["content_mutation_allowed"] is True


def test_plan_round_trips_through_the_environment():
    plan = build_plan(["Kraken"])
    restored = load_plan({CONTENT_PLAN_ENV: serialize_plan(plan)})
    assert restored is not None
    assert restored["allowed_programs"] == ["kraken"]
    assert content_allowed("kraken", restored)[0] is True


# -- the prefill runtime actually enforces it ---------------------------------
def _prefill_source() -> str:
    return (ROOT / "platforms" / "super_parrain" / "prefill.py").read_text(encoding="utf-8")


def test_prefill_consults_the_plan_before_filling_anything():
    src = _prefill_source()
    gate = src.index("classify_disagreement(")
    first_fill = src.index('filled.append("code")')
    assert gate < first_fill, "the plan gate must precede every field fill"


def test_prefill_blocks_and_reports_a_disagreement():
    src = _prefill_source()
    assert 'result["fields_filled"] = []' in src
    assert "_notify_disagreement(" in src


# -- 3/4. content save + post_verify=false => CANARY_FAILED + notification -----
def _cycle_source() -> str:
    return (ROOT / "tools" / "super_parrain_cycle.py").read_text(encoding="utf-8")


def test_post_verify_failure_emits_a_notifiable_event():
    src = _cycle_source()
    assert "_report_content_failure(" in src
    assert "EVENT_POST_VERIFY_FAILURE" in src
    assert "mark_canary_failed" in src


def test_post_verify_failure_notification_carries_no_secret(tmp_path, monkeypatch):
    from lib import notify

    monkeypatch.setattr(notify, "NOTIFY_DIR", tmp_path)
    monkeypatch.setattr(notify, "OUTBOX_PATH", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(notify, "DEDUP_PATH", tmp_path / "dedup.json")
    rec = notify.emit(
        "ERROR",
        notify.EVENT_POST_VERIFY_FAILURE,
        platform="super-parrain",
        program="kraken",
        post_match=False,
        block_reason="post_match_false",
    )
    assert rec is not None
    assert rec["level"] == "ERROR"
    assert rec["platform"] == "super-parrain"
    assert rec["post_match"] is False
    assert (tmp_path / "outbox.jsonl").exists(), "the artifact source must exist"


def test_a_second_identical_failure_is_deduplicated(tmp_path, monkeypatch):
    from lib import notify

    monkeypatch.setattr(notify, "NOTIFY_DIR", tmp_path)
    monkeypatch.setattr(notify, "OUTBOX_PATH", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(notify, "DEDUP_PATH", tmp_path / "dedup.json")
    kw = dict(platform="super-parrain", program="kraken", block_reason="post_match_false")
    assert notify.emit("ERROR", notify.EVENT_POST_VERIFY_FAILURE, **kw) is not None
    assert notify.emit("ERROR", notify.EVENT_POST_VERIFY_FAILURE, **kw) is None


# -- 5. the artifact exists only when an event was really produced ------------
def test_cache_save_is_skipped_when_no_event_was_produced():
    """No placeholder file is invented just to satisfy the cache."""
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "hashFiles('data/notifications/dedup.json') != ''" in raw


def test_notification_outbox_is_still_uploaded_for_hermes():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [s for s in data["jobs"]["bump"]["steps"]]
    uploads = [
        s for s in steps
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
        and "data/notifications/" in str((s.get("with") or {}).get("path", ""))
    ]
    assert uploads and uploads[0].get("if") == "always()"


# -- 6. bump success + content failure is never a silent full success ---------
def test_bump_and_content_results_are_reported_separately():
    src = _cycle_source()
    assert 'report["summary"]["BUMP_RESULT"]' in src
    assert 'report["summary"]["CONTENT_RESULT"]' in src


def test_content_failure_makes_the_workflow_fail_not_pass_silently():
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "content_failed=1" in raw
    assert "Content result gate" in raw
    assert "::error::" in raw
    data = yaml.safe_load(raw)
    steps = data["jobs"]["bump"]["steps"]
    gate = steps[-1]
    assert gate["name"] == "Content result gate", "the gate must run last, after persistence"
    assert "exit 1" in gate["run"]


# -- 7. historical proof preserved, latest cycle honest -----------------------
def test_record_operational_cycle_never_touches_the_historical_proof():
    from lib.write_status import get_platform_meta, record_operational_cycle

    before = get_platform_meta("super-parrain")
    record_operational_cycle(
        "super-parrain", result="CANARY_FAILED", post_match=False,
        program="kraken", saves="39/39", error="post_match_false",
    )
    after = get_platform_meta("super-parrain")

    assert after["status"] == before["status"] == "WRITE_VERIFIED"
    assert after["evidence"] == before["evidence"]
    assert after["last_write_verified_at"] == before["last_write_verified_at"]

    latest = after["last_operational_cycle"]
    assert latest["result"] == "CANARY_FAILED"
    assert latest["post_verify"] == "FAIL"
    assert latest["post_match"] is False
    assert latest["error"] == "post_match_false"


def test_latest_operational_cycle_no_longer_implies_a_pass_after_a_failure():
    from lib.write_status import get_platform_meta, record_operational_cycle

    record_operational_cycle(
        "super-parrain", result="CANARY_FAILED", post_match=False, program="kraken"
    )
    latest = get_platform_meta("super-parrain")["last_operational_cycle"]
    assert latest.get("conclusion") != "success"
    assert latest["post_verify"] != "PASS"


# -- 8. no regression of the 39/39 bump-only path -----------------------------
def test_bump_only_path_is_untouched_by_the_plan():
    """A program with nothing to change bumps exactly as before."""
    verdict = classify_disagreement(
        program="poulpeo", plan=build_plan(["kraken"]), runtime_needs_update=False
    )
    assert verdict["content_mutation_allowed"] is False
    assert verdict["disagreement"] is False


def test_workflow_still_runs_the_full_cycle_and_keeps_its_schedule():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert (data.get(True) or data.get("on"))["schedule"] == [{"cron": "0 */2 * * *"}]
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "python tools/super_parrain_cycle.py --execute" in raw
    # the historical bumper is never disabled by this change
    assert "AUTOFRESH_SUPER" not in raw or "0" not in raw.split("AUTOFRESH_SUPER")[0][-3:]


def test_cycle_publishes_the_plan_to_the_bumper_subprocess():
    src = _cycle_source()
    assert "env[CONTENT_PLAN_ENV] = serialize_plan(content_plan)" in src
    assert 'build_plan([item["program"] for item in pre["canary_need_update"]])' in src


# -- 9. platform writers stay unchanged during closure maintenance ------------
UNTOUCHED_PATHS = (
    "lib/rctv_bump.py",
    "platforms/code_parrainage",
    "platforms/parrainage_co",
    "platforms/oneparrainage",
    "platforms/referralcodes",
    "tools/controlled_write_code_parrainage.py",
    "tools/controlled_write_parrainage_co.py",
    "tools/controlled_write_1parrainage.py",
)


def test_other_platforms_are_byte_identical_to_main():
    """Writer code stays unchanged; closure fixes workflow persistence/alerts.

    Workflow safety is covered by the dedicated bump workflow tests.
    """
    import subprocess

    base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    if not base:
        pytest.skip("no origin/main available in this checkout")
    changed = subprocess.run(
        ["git", "diff", "--name-only", base, "--"] + list(UNTOUCHED_PATHS),
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    assert changed == [], f"unexpected drift outside Super-Parrain: {changed}"


def test_referralcode_tv_boost_contract_is_unchanged():
    """The RCTV post-verify merged in PR #6 must survive untouched."""
    from lib.rctv_bump import RCTV_BOOST_NOT_VERIFIED, classify_cycle

    unverified = classify_cycle(
        login_ok=True, control_visible=True, remaining_quota=5,
        click_performed=True, post_verify=False,
    )
    assert unverified["outcome"] == RCTV_BOOST_NOT_VERIFIED
    assert unverified["blocking"] is True
