"""Monitor: business compare, multi-field model, stability streak, no-change git. No live network."""
from pathlib import Path

from lib.monitor.engine import (
    MonitorEngine,
    compare_business,
    has_business_change,
    impact_report,
    production_readiness_report,
    save_run_report,
)
from lib.monitor.history import load_last_observations
from lib.monitor.models import (
    Confidence,
    FailureCode,
    FieldChange,
    MonitorProgramStatus,
    Observation,
    ObservationStatus,
    OfferKind,
    SourceClass,
    SourceConfig,
)
from lib.monitor.normalize import normalize_reward
from lib.monitor.parsers.generic_reward_html import parse_generic_reward_html
from lib.monitor.parsers.program_specific import (
    parse_kraken_referral_fr,
    parse_paypal_referral_fr,
)
from lib.monitor.parsers.structured_first import parse_structured_first
from lib.paths import DATA_DIR

FIXTURES = Path(__file__).parent / "fixtures" / "monitor"


def _cfg(program: str = "kraken", parser: str = "generic_reward_html") -> SourceConfig:
    return SourceConfig(
        program=program,
        source_url="https://example.test/referral",
        source_type="official_page",
        extraction_method="http_fetch",
        fields_supported=["referee_reward", "conditions", "min_spend", "qualification_days", "trade_min"],
        parser=parser,
        source_class=SourceClass.VERIFIED_OFFICIAL.value,
        offer_kind=OfferKind.PUBLIC_CAMPAIGN.value,
        impact_count=5,
        source_country="FR",
        source_locale="fr",
        campaign_scope="FR",
        parser_tests_passed=True,
    )


def test_normalize_reward_equivalence():
    assert normalize_reward("20 € en Bitcoin selon l'offre") == normalize_reward(
        "20€ en Bitcoin selon l'offre"
    )


def test_layout_change_same_reward_is_no_business_change():
    html_a = (FIXTURES / "kraken_same_reward.html").read_text(encoding="utf-8")
    html_b = (FIXTURES / "kraken_layout_only.html").read_text(encoding="utf-8")
    cfg = _cfg()
    offer = {"reward": "20 € en Bitcoin selon l’offre", "cond": "new users"}
    a = parse_generic_reward_html(html_a, cfg, offer)
    b = parse_generic_reward_html(html_b, cfg, offer)
    assert a.fields.get("referee_reward")
    assert b.fields.get("referee_reward")
    assert a.raw_fingerprint != b.raw_fingerprint
    assert normalize_reward(a.fields["referee_reward"]) == normalize_reward(
        b.fields["referee_reward"]
    )
    status, changes, _ = compare_business(
        "kraken",
        {"referee_reward": normalize_reward(offer["reward"]), "conditions": None},
        {"referee_reward": normalize_reward(b.fields["referee_reward"])},
        Confidence.HIGH,
    )
    assert status == ObservationStatus.NO_CHANGE
    assert changes == []


def test_reward_change_detected():
    html = (FIXTURES / "kraken_reward_changed.html").read_text(encoding="utf-8")
    cfg = _cfg()
    offer = {"reward": "20 € en Bitcoin selon l’offre"}
    obs = parse_generic_reward_html(html, cfg, offer)
    status, changes, _ = compare_business(
        "kraken",
        {"referee_reward": normalize_reward(offer["reward"])},
        {"referee_reward": normalize_reward(obs.fields.get("referee_reward"))},
        Confidence.HIGH,
    )
    assert status == ObservationStatus.CANDIDATE
    assert any(c.field == "referee_reward" for c in changes)
    assert "50" in (changes[0].new or "")


def test_ambiguous_multi_amounts_review():
    html = (FIXTURES / "ambiguous_multi_amounts.html").read_text(encoding="utf-8")
    cfg = _cfg("generic")
    obs = parse_generic_reward_html(html, cfg, None)
    assert obs.confidence == Confidence.REVIEW
    assert obs.failure_code in {FailureCode.AMBIGUOUS_REWARD, FailureCode.NONE}
    assert any("multiple" in n for n in obs.notes)


def test_error_page_reject():
    html = (FIXTURES / "empty_error.html").read_text(encoding="utf-8")
    obs = parse_generic_reward_html(html, _cfg(), None)
    assert obs.confidence == Confidence.REJECT
    assert obs.failure_code in {
        FailureCode.CHALLENGE,
        FailureCode.DEAD_URL,
        FailureCode.EMPTY_PAGE,
        FailureCode.NO_PUBLIC_OFFER,
    }


def test_refuse_empty_overwrite():
    status, changes, notes = compare_business(
        "kraken",
        {"referee_reward": "20 €"},
        {"referee_reward": None},
        Confidence.HIGH,
    )
    assert status == ObservationStatus.NO_CHANGE
    assert any("refuse_empty" in n or "field_missing" in n for n in notes)


def test_engine_with_html_override_no_network():
    eng = MonitorEngine(live_fetch=False)
    html = (FIXTURES / "kraken_layout_only.html").read_text(encoding="utf-8")
    cfg = _cfg("kraken", parser="kraken_referral_fr")
    eng.registry["kraken"] = cfg
    obs = eng.run_program("kraken", html_override=html)
    assert obs.program == "kraken"
    assert obs.failure_code is not None
    assert obs.impact_count >= 0
    assert obs.status in {
        ObservationStatus.NO_CHANGE,
        ObservationStatus.CANDIDATE,
        ObservationStatus.REVIEW,
        ObservationStatus.REJECTED,
    }


def test_impact_integration_shape():
    eng = MonitorEngine(live_fetch=False)
    eng.registry["kraken"] = _cfg("kraken")
    html = (FIXTURES / "kraken_reward_changed.html").read_text(encoding="utf-8")
    obs = eng.run_program("kraken", html_override=html)
    if not obs.changes:
        obs.changes = [
            FieldChange(field="referee_reward", old="20 €", new="50 € en Bitcoin")
        ]
        obs.status = ObservationStatus.CANDIDATE
    rep = impact_report(obs)
    assert rep["program"] == "kraken"
    assert "platforms" in rep
    assert rep.get("observation_only") is True


def test_multi_field_paypal_fixture():
    html = (FIXTURES / "paypal_multi_field.html").read_text(encoding="utf-8")
    cfg = _cfg("paypal", parser="paypal_referral_fr")
    obs = parse_paypal_referral_fr(html, cfg, None)
    assert obs.fields.get("referee_reward")
    assert obs.fields.get("min_spend")
    assert obs.fields.get("qualification_days")
    assert obs.confidence == Confidence.HIGH
    # must not collapse all amounts into one reward guess
    assert "10" in (obs.fields["referee_reward"] or "")
    assert "5" in (obs.fields["min_spend"] or "")


def test_kraken_parser_program_specific():
    html = (FIXTURES / "kraken_same_reward.html").read_text(encoding="utf-8")
    cfg = _cfg("kraken", parser="kraken_referral_fr")
    obs = parse_kraken_referral_fr(html, cfg, {"reward": "20 € en Bitcoin"})
    assert obs.parser == "kraken_referral_fr"
    assert obs.fields.get("referee_reward")


def test_generic_not_final_when_structured_first():
    html = (FIXTURES / "kraken_same_reward.html").read_text(encoding="utf-8")
    cfg = _cfg("kraken", parser="structured_first")
    obs = parse_structured_first(html, cfg, None)
    # Without structured JSON, generic HIGH is downgraded to REVIEW
    if "structured_single_reward" not in obs.notes:
        assert obs.confidence == Confidence.REVIEW
        assert any("generic_only" in n for n in obs.notes)


def test_fixture_streak_does_not_verify():
    """Replaying the same fixture must not grant MONITOR_VERIFIED (live required)."""
    from lib.monitor.engine import _derive_monitor_status

    cfg = _cfg("kraken", parser="kraken_referral_fr")
    cfg.parser_tests_passed = True
    mon = _derive_monitor_status(
        cfg,
        Confidence.HIGH,
        FailureCode.NONE,
        live_high_streak=5,
        status=ObservationStatus.NO_CHANGE,
        is_live=False,
    )
    assert mon != MonitorProgramStatus.MONITOR_VERIFIED.value


def test_live_streak_can_verify():
    from lib.monitor.engine import _derive_monitor_status

    cfg = _cfg("kraken", parser="kraken_referral_fr")
    cfg.parser_tests_passed = True
    mon = _derive_monitor_status(
        cfg,
        Confidence.HIGH,
        FailureCode.NONE,
        live_high_streak=3,
        status=ObservationStatus.NO_CHANGE,
        is_live=True,
    )
    assert mon == MonitorProgramStatus.MONITOR_VERIFIED.value


def test_uk_source_no_fr_authority():
    cfg = _cfg("paypal")
    cfg.source_country = "UK"
    cfg.campaign_scope = "UK"
    cfg.field_authority_fr = {"referee_reward": False, "conditions": False}
    cfg.field_sources = {
        **cfg.field_sources,
        "referee_reward": "OFFICIAL_PUBLIC_MONITOR",
    }
    assert cfg.has_fr_authority("referee_reward") is False
    assert cfg.monitor_may_write_field("referee_reward") is False


def test_kraken_parser_does_not_prefer_small_btc_over_jusqua():
    html = """
    <html><body>
    Jusqu'à 200 € offerts pour les filleuls.
    Recevez aussi 20 € en Bitcoin selon l'offre.
    </body></html>
    """
    cfg = _cfg("kraken", parser="kraken_referral_fr")
    obs = parse_kraken_referral_fr(html, cfg, None)
    assert obs.confidence == Confidence.REVIEW
    assert any("conflicting" in n for n in obs.notes)
    # Must not treat 20 € BTC as HIGH authority
    assert obs.confidence != Confidence.HIGH


def test_shadow_never_writes_canonical(tmp_path):
    from lib.monitor.shadow import SHADOW_ACCEPT, SHADOW_REJECT, may_write_canonical
    from lib.paths import OFFERS_PATH

    before = OFFERS_PATH.read_text(encoding="utf-8")
    ok, why = may_write_canonical(SHADOW_REJECT, program="kraken", field="referee_reward")
    assert ok is False
    ok2, why2 = may_write_canonical(SHADOW_ACCEPT, program="kraken", field="referee_reward")
    assert ok2 is False
    assert "shadow" in why2 or "operator" in why2 or "never" in why2
    after = OFFERS_PATH.read_text(encoding="utf-8")
    assert after == before


def test_super_parrain_canary_keeps_200_reward():
    from platforms.super_parrain.writer import build_write_plan

    plan = build_write_plan("super-parrain", "kraken", "fr")
    assert "referee_reward" not in (plan.changed_fields or {})
    assert plan.variables.get("referee_reward") == "200 € en cryptomonnaies"
    assert "20 €" not in plan.rendered
    assert "4hpz4gdy" not in plan.rendered
    assert "proinvite.kraken.com" not in plan.rendered
    assert plan.variables.get("personal_code") == "cpbrgddy"
    assert plan.variables.get("personal_link") == "https://invite.kraken.com/JDNW/s5qudqe4"
    assert "Jusqu’à 200 € offerts" in plan.rendered or "Jusqu'à 200 € offerts" in plan.rendered
    assert "200 € en cryptomonnaies" in plan.rendered
    assert not plan.changed_fields


def test_no_business_change_skips_commit_flag(tmp_path, monkeypatch):
    from lib.monitor import engine as eng_mod
    from lib.monitor import history as hist_mod

    monkeypatch.setattr(eng_mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(eng_mod, "LAST_OBS_PATH", tmp_path / "last.json")
    monkeypatch.setattr(eng_mod, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(hist_mod, "LAST_OBS_PATH", tmp_path / "last.json")
    monkeypatch.setattr(hist_mod, "HISTORY_PATH", tmp_path / "history.jsonl")

    html = (FIXTURES / "kraken_layout_only.html").read_text(encoding="utf-8")
    eng = MonitorEngine(live_fetch=False)
    eng.registry["kraken"] = _cfg("kraken", parser="kraken_referral_fr")
    o1 = eng.run_program("kraken", html_override=html)
    # Normalize to stable business state (NO_CHANGE, no field diffs)
    o1.status = ObservationStatus.NO_CHANGE
    o1.changes = []
    o1.confidence = Confidence.HIGH
    o1.failure_code = FailureCode.NONE
    o1.monitor_status = MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value
    o1.high_streak = 1
    o1.live_high_streak = 1
    p1 = save_run_report([o1], path=tmp_path / "report.json")
    data1 = __import__("json").loads(p1.read_text(encoding="utf-8"))
    assert data1["should_commit"] is True  # first run always commits

    eng.last = load_last_observations()
    o2 = eng.run_program("kraken", html_override=html)
    # identical business/status signature (only detected_at would differ)
    o2.business_fingerprint = o1.business_fingerprint
    o2.status = o1.status
    o2.confidence = o1.confidence
    o2.failure_code = o1.failure_code
    o2.monitor_status = o1.monitor_status
    o2.high_streak = o1.high_streak
    o2.live_high_streak = o1.live_high_streak
    o2.changes = []
    assert has_business_change([o2], data1) is False
    p2 = save_run_report([o2], path=tmp_path / "report.json")
    data2 = __import__("json").loads(p2.read_text(encoding="utf-8"))
    assert data2["should_commit"] is False


def test_field_authority_drops_personal_like():
    eng = MonitorEngine(live_fetch=False)
    cfg = _cfg("kraken")
    cfg.field_sources = {
        **cfg.field_sources,
        "referee_reward": "OPERATOR",
    }
    eng.registry["kraken"] = cfg
    html = (FIXTURES / "kraken_same_reward.html").read_text(encoding="utf-8")
    obs = eng.run_program("kraken", html_override=html)
    # monitor may not claim OPERATOR fields
    assert "referee_reward" not in obs.observed_fields or any(
        "field_authority" in n for n in obs.notes
    )


def test_zero_reward_not_plausible():
    from lib.monitor.normalize import is_plausible_reward

    assert is_plausible_reward("0 €") is False
    assert is_plausible_reward("0,00 €") is False
    assert is_plausible_reward("20 €") is True


def test_swissborg_rejects_converter_false_positive():
    """Homepage converter amounts (EUR €100) must not become HIGH referral reward."""
    html = """
    <html><body>
    <h1>SwissBorg</h1>
    <p>Tu dépenses EUR €100 €250 €500 Tu reçois BTC</p>
    </body></html>
    """
    from lib.monitor.parsers.program_specific import parse_swissborg_referral_fr

    cfg = _cfg("swissborg", parser="swissborg_referral_fr")
    obs = parse_swissborg_referral_fr(html, cfg, None)
    assert obs.confidence != Confidence.HIGH
    assert not obs.fields.get("referee_reward")


def test_winamax_compound_reward_normalize():
    from lib.monitor.normalize import normalize_reward

    assert "freebet" in (normalize_reward("10 € bonus + 10 € freebets") or "").lower()


def test_production_report_shape():
    results = [
        Observation(
            program="kraken",
            status=ObservationStatus.NO_CHANGE,
            confidence=Confidence.HIGH,
            source_url="https://x",
            parser="kraken_referral_fr",
            detected_at="t",
            canonical_fields={},
            observed_fields={"referee_reward": "20 €"},
            monitor_status=MonitorProgramStatus.MONITOR_VERIFIED.value,
            high_streak=3,
            live_high_streak=3,
            impact_count=5,
            source_class=SourceClass.VERIFIED_OFFICIAL.value,
            is_live=True,
            parser_tests_passed=True,
        ),
        Observation(
            program="revolut",
            status=ObservationStatus.SKIPPED,
            confidence=Confidence.REJECT,
            source_url="https://y",
            parser="app_personalized_stub",
            detected_at="t",
            canonical_fields={},
            observed_fields={},
            failure_code=FailureCode.APP_ONLY,
            monitor_status=MonitorProgramStatus.APP_PERSONALIZED.value,
            impact_count=5,
            offer_kind=OfferKind.APP_PERSONALIZED.value,
            source_class=SourceClass.AUTH_APP_ONLY.value,
        ),
    ]
    # pad to 30 with final non-pending statuses
    for i in range(28):
        results.append(
            Observation(
                program=f"p{i}",
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=None,
                parser="operator_only_stub",
                detected_at="t",
                canonical_fields={},
                observed_fields={},
                monitor_status=MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value,
                impact_count=1,
            )
        )
    prod = production_readiness_report(results)
    assert prod["programs"] == 30
    assert prod["MONITOR_VERIFIED"] == 1
    assert prod["APP_PERSONALIZED"] == 1
    assert prod["PUBLIC_MONITORABLE_PENDING"] == 0
    assert "MONITORING_PRODUCTION_READY" in prod
    assert prod["MONITORING_BASE_READY"] == "YES"
    assert "public_mutable_mapping_coverage" in prod
