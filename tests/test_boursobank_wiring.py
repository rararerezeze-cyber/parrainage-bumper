"""BoursoBank native markers — golden match, no invented phrases."""
from __future__ import annotations

import json

from lib.monitor.auto_accept import observations_from_last_report, simulate
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import structure_preserved_via_markers
from platforms.super_parrain.writer import build_write_plan


def test_boursobank_campaign_variant_and_reward_type_not_mutable():
    maps = MappingRepository()
    for platform, lang in (
        ("1parrainage", "fr"),
        ("parrainage-co", "fr"),
        ("super-parrain", "fr"),
        ("referralcode-tv", "en"),
    ):
        mapping = maps.load(platform, "boursobank", lang)
        assert "campaign_variant" not in mapping.mutable_fields
        assert "reward_type" not in mapping.mutable_fields


def test_boursobank_rctv_has_no_invented_amount_markers():
    mapping = MappingRepository().load("referralcode-tv", "boursobank", "en")
    template = TemplateRepository().load_text("referralcode-tv", "boursobank", "en")
    assert mapping.mutable_fields == []
    assert "{{REFEREE_REWARD}}" not in template
    assert "{{MIN_DEPOSIT}}" not in template
    assert "{{QUALIFICATION_DAYS}}" not in template


def test_boursobank_render_matches_golden(tmp_path, monkeypatch):
    ov = tmp_path / "operator-overrides.json"
    ov.write_text('{"version":1,"overrides":[]}\n', encoding="utf-8")
    acc = tmp_path / "accepted-fields.json"
    acc.write_text('{"version":1,"programs":{}}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr("lib.operator_overrides.ACCEPTED_MONITOR_FIELDS_PATH", acc)

    renderer = Renderer(OffersRepository())
    maps = MappingRepository()
    templates = TemplateRepository()
    for platform in ("1parrainage", "parrainage-co", "super-parrain"):
        mapping = maps.load(platform, "boursobank", "fr")
        template = templates.load_text(platform, "boursobank", "fr")
        golden = templates.load_golden(platform, "boursobank", "fr")
        rendered = renderer.render(template, mapping)
        assert rendered == golden, platform
        assert structure_preserved_via_markers(
            template,
            golden,
            rendered,
            mapping.mutable_fields,
            mapping.markers,
            mapping.platform_values or {},
            renderer.build_variables(mapping),
        )


def test_boursobank_1p_has_no_min_deposit_or_days():
    mapping = MappingRepository().load("1parrainage", "boursobank", "fr")
    assert mapping.mutable_fields == ["referee_reward"]
    template = TemplateRepository().load_text("1parrainage", "boursobank", "fr")
    assert "{{MIN_DEPOSIT}}" not in template
    assert "{{QUALIFICATION_DAYS}}" not in template


def test_boursobank_simulate_only_real_native_diffs(tmp_path, monkeypatch):
    ov = tmp_path / "operator-overrides.json"
    ov.write_text(json.dumps({"version": 1, "overrides": []}), encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    acc = tmp_path / "accepted-fields.json"
    acc.write_text('{"version":1,"programs":{}}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.ACCEPTED_MONITOR_FIELDS_PATH", acc)

    report = simulate(observations_from_last_report(), persist_report=False)
    assert report["live_writes_performed"] == 0
    assert report["switch_enabled"] is False
    bourso = [
        d
        for d in report["simulated_safe_diffs"]
        if d["program"] == "boursobank"
    ]
    assert bourso, "expected BoursoBank referee_reward diffs after wiring"
    for d in bourso:
        assert "campaign_variant" not in d["changed_fields"]
        assert "reward_type" not in d["changed_fields"]
        assert "min_deposit" not in d["changed_fields"]
        assert "qualification_days" not in d["changed_fields"]
        assert d["changed_fields"]["referee_reward"]["old"] == "200 €"
        assert d["changed_fields"]["referee_reward"]["new"] == "160 €"


def test_super_poulpeo_canary_all_reward_spans_consistent():
    """referee_reward override stays internally consistent across every span.

    Deliberately reads the *current* published value from the mapping file
    instead of hardcoding it: a real WRITE_VERIFIED canary changed Poulpeo's
    referee_reward from 5€ to 3€ on 2026-08-13 (GH run 31724917509,
    data/captures/write-super-parrain-poulpeo.json), which broke a previous
    version of this test that hardcoded old="5€". A canary content field is
    live production state, not a stable test fixture -- this test must keep
    passing regardless of what the currently-published reward happens to be.
    """
    from lib.paths import mapping_path

    current = json.loads(
        mapping_path("super-parrain", "poulpeo", "fr").read_text(encoding="utf-8")
    )
    old_reward = current["platform_values"]["referee_reward"]
    # Probe value guaranteed different from whatever is currently published.
    new_reward = "9€" if old_reward != "9€" else "8€"

    plan = build_write_plan(
        "super-parrain",
        "poulpeo",
        "fr",
        overrides={"referee_reward": f"{new_reward[:-1]} €"},
        only_fields=["referee_reward"],
    )
    assert plan.structure_preserved
    assert set(plan.changed_fields) == {"referee_reward"}
    assert plan.changed_fields["referee_reward"]["old"] == old_reward
    assert plan.changed_fields["referee_reward"]["new"] == new_reward
    assert plan.historical.count(old_reward) == 3
    assert plan.rendered.count(new_reward) == 3
    assert plan.rendered.count(old_reward) == 0
    assert "4KD2ab" in plan.rendered
    assert "sponsor_key=4KD2ab" in plan.rendered
    assert plan.variables["personal_code"] == "4KD2ab"
    assert "sponsor_key=4KD2ab" in (plan.variables["personal_link"] or "")
    assert "{{" not in plan.historical
    assert "{{" not in plan.rendered
    expected = plan.historical.replace(old_reward, new_reward)
    assert plan.rendered == expected
