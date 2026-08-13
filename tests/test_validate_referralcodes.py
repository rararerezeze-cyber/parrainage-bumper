"""Agent Import validate-only helpers. No live Commit."""
import json

from tools.validate_referralcodes_agent_import import _classify_result, _kraken_item


def test_kraken_item_keeps_native_en_200():
    item = _kraken_item()
    assert item["shop"] == "kraken"
    assert item["code"] == "cpbrgddy"
    assert "s5qudqe4" in item["url"]
    assert "200 €" not in (item["discount"] or "")
    assert "$200" in (item["discount"] or "")


def test_classify_update_vs_duplicate():
    assert _classify_result("will update existing listing")["update_or_duplicate"] == "UPDATE_OR_MATCH"
    assert _classify_result("duplicate shop already listed")["update_or_duplicate"] == "DUPLICATE_RISK"
    assert _classify_result("will add new listing")["update_or_duplicate"] == "WOULD_CREATE"
    assert _classify_result("error: invalid shop")["update_or_duplicate"] == "VALIDATE_ERROR"
    parsed = {
        "draft_id": 124,
        "summary": {"total": 1, "valid": 1, "invalid": 0},
        "items": [{"index": 0, "shop_id": 4769, "shop_name": "Kraken", "errors": []}],
    }
    c = _classify_result(json.dumps(parsed), parsed)
    assert c["existing_detected"] is True
    assert c["update_or_duplicate"] == "SHOP_MATCHED_UPDATE_UNKNOWN"
    assert c["signals"]["listing_update_proven"] is False
