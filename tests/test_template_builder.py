from lib.template_builder import build_from_text


def test_build_marks_code_link_reward():
    golden = (
        "Offre test\n"
        "⚡ Bonus : 50 € offerts ⭐️\n"
        "✅ Code parrain : ABC123\n"
        "✅ Lien :\n"
        "https://example.com/r/ABC123\n"
    )
    offer = {
        "lk": "demo",
        "code": "ABC123",
        "link": "https://example.com/r/ABC123",
        "reward": "50 € offerts",
    }
    result = build_from_text(
        platform="super-parrain",
        program="demo",
        language="fr",
        golden_text=golden,
        offer=offer,
        announcement_url="https://example.com/a",
    )
    assert "personal_code" in result.mutable_fields
    assert "personal_link" in result.mutable_fields
    assert "{{PERSONAL_CODE}}" in result.template
    assert "ABC123" not in result.template
    assert result.golden == golden


def test_ambiguous_reward_left_fixed():
    golden = "Promo jusqu'a 200 € et encore 200 € ailleurs sans code clair."
    offer = {"lk": "x", "code": None, "link": None, "reward": "200 €"}
    result = build_from_text(
        platform="super-parrain",
        program="x",
        language="fr",
        golden_text=golden,
        offer=offer,
    )
    # 200 € appears twice → should not mark short multi-hit reward from offer if count>1
    # offer reward "200 €" count is 2
    assert "referee_reward" not in result.mutable_fields or result.confidences.get("referee_reward") != "high"
