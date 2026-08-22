from __future__ import annotations

from pathlib import Path

from tools import preflight_1parrainage_headless as preflight


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "preflight_1parrainage_headless.py"
WORKFLOW = ROOT / ".github" / "workflows" / "preflight_1parrainage_headless.yml"


def test_strict_control_filter_requires_exact_visible_enabled_edit_submit():
    valid = {
        "label": "Envoyer",
        "visible": True,
        "enabled": True,
        "form": {"matches_edit_form": True},
    }
    census = {
        "save_term_controls": [
            valid,
            {**valid, "label": "Enregistrer"},
            {**valid, "enabled": False},
            {**valid, "form": {"matches_edit_form": False}},
        ]
    }
    assert preflight._strict_edit_save_controls(census) == [valid]


def test_preflight_hard_codes_exact_fresh_baselines():
    assert preflight.EXPECTED_ACCOUNT_LEN == 1062
    assert (
        preflight.EXPECTED_ACCOUNT_SHA256
        == "ad2a57ac0e2afc795ca038c936ac2f63a93faaeb141a356bb9692fcf16598afb"
    )
    assert preflight.EXPECTED_NORMALIZED_LEN == 1063
    assert (
        preflight.EXPECTED_NORMALIZED_SHA256
        == "48d1ad78c24536e00e4e2bb8b5fd674b7a65466d6c16f5a72e7772affd0af3e7"
    )


def test_preflight_is_read_only_and_fail_closed():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "_read_account" in source
    assert "_public_evidence" in source
    assert "_resolve_save_control" in source
    assert '"preflight_pass": False' in source
    assert '"platform_writes": 0' in source
    assert '"save_clicks": 0' in source
    assert '"edit_form_submits_after_login": 0' in source
    for forbidden in (
        "_click_save_once",
        "_attempt_save_click",
        "_set_body_without_save",
        "execute_write",
        "save_write_status",
        "record_live_failure",
        "record_live_success",
    ):
        assert forbidden not in source


def test_workflow_is_manual_read_only_and_cannot_dispatch_canary():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "schedule:" not in source
    assert "contents: read" in source
    assert 'AUTOFRESH_LIVE_WRITES: "0"' in source
    assert "tools/preflight_1parrainage_headless.py" in source
    assert "canary_write_1parrainage.py" not in source
    assert "WRITE_1P_CANARY_ROLLBACK" not in source
    assert "git push" not in source
