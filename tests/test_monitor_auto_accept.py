"""Deterministic monitor auto-accept — simulation, no live write."""
from __future__ import annotations

import json

from lib.monitor.auto_accept import (
    INITIAL_SCOPE,
    apply_accepts,
    auto_accept_enabled,
    evaluate_field,
    simulate,
)
from lib.monitor.models import (
    Confidence,
    FailureCode,
    FieldChange,
    Observation,
    ObservationStatus,
)
from lib.operator_overrides import OperatorOverrideStore


def _obs(**kwargs) -> Observation:
    defaults = dict(
        program="winamax",
        status=ObservationStatus.CANDIDATE,
        confidence=Confidence.HIGH,
        source_url="https://www.winamax.fr/parrainage",
        parser="winamax_parrainage_fr",
        detected_at="2026-08-12T00:00:00+00:00",
        canonical_fields={"referee_reward": "100 €"},
        observed_fields={"referee_reward": "10 € bonus + 10 € freebets"},
        changes=[
            FieldChange(
                field="referee_reward", old="100 €", new="10 € bonus + 10 € freebets"
            )
        ],
        failure_code=FailureCode.NONE,
        source_class="VERIFIED_OFFICIAL",
        offer_kind="PUBLIC_CAMPAIGN",
        monitor_status="MONITOR_VERIFIED",
        live_high_streak=6,
        impact_count=4,
        parser_tests_passed=True,
        source_country="FR",
        source_locale="fr",
        campaign_scope="FR",
        field_authority={"referee_reward": "OFFICIAL_PUBLIC_MONITOR"},
    )
    defaults.update(kwargs)
    return Observation(**defaults)


def test_switch_defaults_off():
    assert auto_accept_enabled() is False


def test_kraken_reward_never_accepted(tmp_path, monkeypatch):
    store = OperatorOverrideStore(path=tmp_path / "ov.json")
    store.upsert("kraken", "referee_reward", "200 € en cryptomonnaies")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", store.path)
    cand = {
        "program": "kraken",
        "field": "referee_reward",
        "canonical": "20 € en Bitcoin",
        "observed": "20 € en Bitcoin",
        "authority": "OFFICIAL_PUBLIC_MONITOR",
        "source_country": "GLOBAL",
        "source_locale": "fr",
        "campaign_scope": "FR",
        "live_high_streak": 7,
        "parser": "kraken_referral_fr",
    }
    obs = _obs(
        program="kraken",
        source_url="https://www.kraken.com/referrals",
        parser="kraken_referral_fr",
        source_country="GLOBAL",
        campaign_scope="FR",
        field_authority={"referee_reward": "OFFICIAL_PUBLIC_MONITOR"},
        observed_fields={"referee_reward": "20 € en Bitcoin"},
        changes=[FieldChange(field="referee_reward", old="20 € en Bitcoin", new="20 € en Bitcoin")],
    )
    r = evaluate_field(cand, obs, store=store)
    assert r["decision"] == "REJECT"
    assert any("operator" in x or "locked" in x or "20eur" in x for x in r["reasons"])


def test_boursobank_campaign_variant_unknown_rejected():
    cand = {
        "program": "boursobank",
        "field": "campaign_variant",
        "observed": "public_bon_plan",
        "authority": "UNKNOWN",
        "source_country": "FR",
        "source_locale": "fr",
        "campaign_scope": "FR",
        "live_high_streak": 6,
        "parser": "boursobank_parrainage_fr",
    }
    r = evaluate_field(cand, _obs(program="boursobank"), store=OperatorOverrideStore())
    assert r["decision"] == "REJECT"
    assert any("authority" in x or "campaign_variant" in x for x in r["reasons"])


def test_personal_fields_rejected():
    cand = {
        "program": "winamax",
        "field": "personal_code",
        "observed": "AD8RAY",
        "authority": "TELEGRAM_OPERATOR",
        "source_country": "FR",
        "source_locale": "fr",
        "campaign_scope": "FR",
        "live_high_streak": 6,
    }
    r = evaluate_field(cand, _obs(), store=OperatorOverrideStore())
    assert r["decision"] == "REJECT"


def test_apply_blocked_when_switch_off():
    out = apply_accepts(
        [{"program": "winamax", "field": "referee_reward", "observed": "10 €"}],
        force=False,
    )
    assert out["applied"] is False
    assert out["live_writes_performed"] == 0


def test_simulate_scope_only():
    assert "boursobank" in INITIAL_SCOPE
    assert "winamax" in INITIAL_SCOPE
    report = simulate([], persist_report=False)
    assert report["live_writes_performed"] == 0
    assert report["switch_enabled"] is False


def test_simulate_does_not_enable_switch():
    from lib.phase import load_phase

    assert load_phase().get("monitor_auto_accept") is False
    assert auto_accept_enabled() is False
