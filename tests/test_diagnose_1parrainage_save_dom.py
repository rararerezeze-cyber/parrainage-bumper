from __future__ import annotations

from pathlib import Path

from tools import diagnose_1parrainage_save_dom as diagnostic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "diagnose_1parrainage_save_dom.py"
WORKFLOW = ROOT / ".github" / "workflows" / "diagnose_1parrainage_save_dom.yml"


def _control(label: str, *, visible: bool = True):
    return {
        "tag": "button",
        "id": "save",
        "name": "save",
        "type": "submit",
        "label": label,
        "class": "btn",
        "visible": visible,
        "enabled": True,
        "disabled": False,
        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
        "in_viewport": visible,
        "form": {"action": "https://www.1parrainage.com/espace_parrain/parrainages/edit/2541207/"},
    }


def test_census_comparison_reports_visibility_without_exposing_body():
    before = {"edit_form_count": 1, "control_count": 1, "controls": [_control("Envoyer")]}
    after = {
        "edit_form_count": 1,
        "control_count": 1,
        "controls": [_control("Envoyer", visible=False)],
    }
    result = diagnostic.compare_census(before, after)
    assert result["added_controls"] == []
    assert result["removed_controls"] == []
    assert result["changed_controls"] == [
        {"control": "button|save|save|submit|Envoyer|btn", "fields": ["visible", "in_viewport"]}
    ]


def test_diagnostic_reaches_three_checkpoints_without_save_path():
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.index('"A_AFTER_EDIT_OPEN"') < source.index(
        '"B_AFTER_ACCOUNT_NORMALIZATION"'
    ) < source.index('"C_AFTER_MARKER_PREP"')
    assert "_read_account(" in source
    assert "_set_body_without_save(" in source
    for forbidden in (
        "_click_save_once",
        "_attempt_save_click",
        "_resolve_save_control",
        "execute_write",
        "save_write_status",
        "record_live_failure",
    ):
        assert forbidden not in source
    assert '"platform_writes": 0' in source
    assert '"save_clicks": 0' in source


def test_workflow_is_manual_read_only_and_uses_no_canary_command():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "schedule:" not in source
    assert "contents: read" in source
    assert 'AUTOFRESH_LIVE_WRITES: "0"' in source
    assert "tools/diagnose_1parrainage_save_dom.py" in source
    assert "canary_write_1parrainage.py" not in source
    assert "WRITE_1P_CANARY_ROLLBACK" not in source
    assert "git push" not in source
