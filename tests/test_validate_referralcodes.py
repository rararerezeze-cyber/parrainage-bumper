"""Agent Import validate-only helpers. No live Commit."""
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
    assert _classify_result("will add new item")["update_or_duplicate"] == "WOULD_CREATE"
    assert _classify_result("error: invalid shop")["update_or_duplicate"] == "VALIDATE_ERROR"
    assert _classify_result("ok")["update_or_duplicate"] == "UNKNOWN"
