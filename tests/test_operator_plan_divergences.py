"""Telegram `Kraken status` must surface real BUSINESS divergences and the
last write/error per platform -- not the 100+ ENGINE_METADATA candidates a
capture run can produce. See lib.mapping_candidates.classify_field().

Uses the real kraken/1parrainage mapping already in the repo (via the
session-wide data/ sandbox in tests/conftest.py), the same pattern as
tests/test_operator_overrides.py::test_telegram_set_and_plan. Each test
gets its own isolated mapping-candidates store (function-scoped monkeypatch
layered on top of the session sandbox) so candidates recorded by one test
never leak into another.
"""
from __future__ import annotations

import pytest

from lib.mapping_candidates import record_candidate_divergence
from lib.operator_plan import format_plan_report, plan_program_impact
from lib.write_status import load_write_status, save_write_status


@pytest.fixture(autouse=True)
def isolated_candidates_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lib.mapping_candidates.MAPPING_CANDIDATES_PATH",
        tmp_path / "mapping-candidates.json",
    )
    # write_status.STATUS_PATH is also session-sandboxed (not per-test) --
    # a test in this file writing last_write_verified_at/last_failure for
    # 1parrainage must never leak into other test files' expectations about
    # that platform's default status.
    monkeypatch.setattr(
        "lib.write_status.STATUS_PATH",
        tmp_path / "platform-write-status.json",
    )


def test_business_divergence_surfaces_in_plan():
    record_candidate_divergence(
        "1parrainage",
        "kraken",
        "fr",
        "platform_values.referee_reward",
        "200 € en cryptomonnaies",
        "250 € en cryptomonnaies",
    )
    record_candidate_divergence(
        "1parrainage", "kraken", "fr", "confidences.personal_code", "medium", "low"
    )
    record_candidate_divergence("1parrainage", "kraken", "fr", "sync_mode", "REVIEW", "SAFE_AUTO")

    plan = plan_program_impact("kraken", platform_filter="1parrainage")
    fields = {d["field"] for d in plan["business_divergences"]}
    assert fields == {"platform_values.referee_reward"}
    assert plan["summary"]["business_divergences_pending"] == 1

    report = format_plan_report("kraken", None, None, None, plan, action="status")
    assert "BUSINESS divergences detected (1)" in report
    assert "referee_reward" in report
    assert "confidences.personal_code" not in report
    assert "sync_mode" not in report.split("BUSINESS divergences detected")[-1]


def test_no_pending_business_divergence_means_empty_list():
    plan = plan_program_impact("kraken", platform_filter="1parrainage")
    assert plan["business_divergences"] == []
    assert plan["summary"]["business_divergences_pending"] == 0


def test_last_write_verified_and_last_failure_surface_per_platform():
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault("1parrainage", {})
    meta["last_write_verified_at"] = "2026-08-10T00:00:00+00:00"
    meta["last_failure"] = {"error": "captcha_timeout", "at": "2026-08-11T00:00:00+00:00"}
    save_write_status(data)

    plan = plan_program_impact("kraken", platform_filter="1parrainage")
    row = next(p for p in plan["platforms"] if p["platform"] == "1parrainage")
    assert row["last_write_verified_at"] == "2026-08-10T00:00:00+00:00"
    assert row["last_failure"]["error"] == "captcha_timeout"

    report = format_plan_report("kraken", None, None, None, plan, action="status")
    assert "last write verified: 2026-08-10T00:00:00+00:00" in report
    assert "last error: captcha_timeout" in report


def test_auto_writer_line_is_built_from_live_state_not_a_hardcoded_list():
    """Regression: the summary named only 1parrainage/code-parrainage long after
    parrainage-co became WRITE_VERIFIED + PC_OFF_READY."""
    import inspect

    from lib import operator_plan

    source = inspect.getsource(operator_plan)
    assert "(1parrainage, code-parrainage)" not in source
    assert "telegram_live_capable" in source
