from __future__ import annotations

from pathlib import Path

from tools.capture_auth_readonly import _rctv_classify_account_links


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tools" / "capture_auth_readonly.py"
WORKFLOW = ROOT / ".github" / "workflows" / "capture_readonly.yml"


def test_rctv_link_classifier_keeps_only_inventory_pages_and_existing_edits():
    rows = [
        {"href": "https://www.referralcode.tv/my-account/?tab=listings"},
        {"href": "https://www.referralcode.tv/my-account/?tab=listings&pagination=2"},
        {"href": "https://referralcode.tv/my-account/page/3/?tab=listings"},
        {"href": "https://www.referralcode.tv/my-account/?pagination=4"},
        {"href": "https://www.referralcode.tv/my-account/page/5/"},
        {"href": "https://www.referralcode.tv/add-referral-code/?eid=23004"},
        {"href": "https://www.referralcode.tv/add-referral-code/"},
        {"href": "https://www.referralcode.tv/add-referral-code/?eid=abc"},
        {"href": "https://www.referralcode.tv/add-referral-code/?eid=23004&action=delete"},
        {"href": "https://www.referralcode.tv/my-account/?tab=dashboard"},
        {"href": "https://www.referralcode.tv/my-account/?tab=listings&action=remove"},
        {"href": "https://www.referralcode.tv/remove/?eid=23004"},
        {"href": "https://evil.example/add-referral-code/?eid=23004"},
        {"href": "http://www.referralcode.tv/add-referral-code/?eid=23004"},
        {"href": "https://www.referralcode.tv:444/add-referral-code/?eid=23004"},
    ]
    result = _rctv_classify_account_links(rows)
    assert result["pages"] == [
        "https://referralcode.tv/my-account/page/3/?tab=listings",
        "https://www.referralcode.tv/my-account/?pagination=4",
        "https://www.referralcode.tv/my-account/?tab=listings",
        "https://www.referralcode.tv/my-account/?tab=listings&pagination=2",
        "https://www.referralcode.tv/my-account/page/5/",
    ]
    assert result["edits"] == [
        "https://www.referralcode.tv/add-referral-code/?eid=23004"
    ]


def test_rctv_capture_is_read_only_paginated_and_preserves_existing_mappings():
    source = CAPTURE.read_text(encoding="utf-8")
    block = source[
        source.index("async def capture_referralcode_tv") : source.index(
            "async def amain"
        )
    ]
    assert '_collect_rctv_inventory_pages(' in block
    assert 'wait_until="networkidle"' not in block
    assert "_save_new_rctv_result" in block
    assert "existing_mapping_preserved" in source
    assert 'page.locator("#cliccami")' not in block
    assert 'button:has-text("Save")' not in block
    assert ".click()" not in block
    # The only form submission is the legitimate login control, before any
    # inventory/edit navigation. Existing-code pages are GET-only.
    assert block.count("human_click(") == 1


def test_workflow_uses_one_authenticated_rctv_capture_session():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "python tools/capture_auth_readonly.py" in workflow
    assert "python tools/probe_referralcode_tv_edit.py --public --auth" not in workflow
