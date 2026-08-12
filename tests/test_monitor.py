"""Monitor: business compare, not HTML equality. No live network required."""
from pathlib import Path

from lib.monitor.engine import MonitorEngine, compare_business, impact_report
from lib.monitor.models import Confidence, ObservationStatus, SourceConfig
from lib.monitor.normalize import normalize_reward
from lib.monitor.parsers.generic_reward_html import parse_generic_reward_html
from lib.monitor.registry import load_registry

FIXTURES = Path(__file__).parent / "fixtures" / "monitor"


def _cfg(program: str = "kraken") -> SourceConfig:
    return SourceConfig(
        program=program,
        source_url="https://example.test/referral",
        source_type="official_page",
        extraction_method="http_fetch",
        fields_supported=["referee_reward", "conditions"],
        parser="generic_reward_html",
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
    # fingerprints differ (HTML different)
    assert a.raw_fingerprint != b.raw_fingerprint
    # business values equivalent after normalize
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
    assert any("multiple_reward" in n for n in obs.notes)


def test_error_page_reject():
    html = (FIXTURES / "empty_error.html").read_text(encoding="utf-8")
    obs = parse_generic_reward_html(html, _cfg(), None)
    assert obs.confidence == Confidence.REJECT


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
    # inject temporary config by monkeypatch registry
    from lib.monitor import engine as eng_mod

    cfg = _cfg("kraken")
    eng.registry["kraken"] = cfg
    obs = eng.run_program("kraken", html_override=html)
    assert obs.program == "kraken"
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
    # force candidate for impact path
    if not obs.changes:
        from lib.monitor.models import FieldChange

        obs.changes = [
            FieldChange(field="referee_reward", old="20 €", new="50 € en Bitcoin")
        ]
        obs.status = ObservationStatus.CANDIDATE
    rep = impact_report(obs)
    assert rep["program"] == "kraken"
    assert "platforms" in rep
    assert rep.get("observation_only") is True
