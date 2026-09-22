"""Deterministic monitor auto-accept — simulation, no live write."""
from __future__ import annotations

import json

from lib.monitor.auto_accept import (
    INITIAL_SCOPE,
    apply_accepts,
    auto_accept_enabled,
    effective_sync_routes,
    evaluate_all,
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


def test_switch_enabled_in_production_batch_mode():
    assert auto_accept_enabled() is True


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


def test_emergency_switch_off_blocks_apply(monkeypatch):
    monkeypatch.setenv("AUTOFRESH_MONITOR_AUTO_ACCEPT", "0")
    out = apply_accepts(
        [{"program": "winamax", "field": "referee_reward", "observed": "10 €"}],
        force=False,
    )
    assert out["applied"] is False
    assert out["live_writes_performed"] == 0


def test_legacy_pilot_scope_remains_available_for_explicit_debug():
    assert "boursobank" in INITIAL_SCOPE
    assert "winamax" in INITIAL_SCOPE
    report = simulate([], persist_report=False)
    assert report["live_writes_performed"] == 0
    assert report["switch_enabled"] is True


def test_phase_enables_global_verified_batch():
    from lib.phase import load_phase

    assert load_phase().get("monitor_auto_accept") is True
    assert auto_accept_enabled() is True



def test_default_scope_accepts_verified_program_outside_legacy_pilot(tmp_path):
    store = OperatorOverrideStore(path=tmp_path / "ov.json")
    cand = {
        "program": "newbrand",
        "field": "referee_reward",
        "canonical": "5 €",
        "observed": "10 €",
        "authority": "OFFICIAL_PUBLIC_MONITOR",
        "source_country": "FR",
        "source_locale": "fr",
        "campaign_scope": "FR",
        "live_high_streak": 6,
        "parser": "newbrand_referral_fr",
    }
    obs = _obs(
        program="newbrand",
        parser="newbrand_referral_fr",
        canonical_fields={"referee_reward": "5 €"},
        observed_fields={"referee_reward": "10 €"},
        changes=[FieldChange(field="referee_reward", old="5 €", new="10 €")],
    )
    result = evaluate_field(cand, obs, store=store, accepted={})
    assert result["decision"] == "ACCEPT"

    scoped = evaluate_field(
        cand,
        obs,
        store=store,
        accepted={},
        scope=INITIAL_SCOPE,
    )
    assert scoped["decision"] == "REJECT"
    assert "outside_explicit_scope:newbrand" in scoped["reasons"]


def test_evaluate_all_default_scope_is_all_verified(tmp_path, monkeypatch):
    store = OperatorOverrideStore(path=tmp_path / "ov.json")
    monkeypatch.setattr(
        "lib.monitor.auto_accept.load_accepted_monitor_fields",
        lambda: {},
    )
    obs = _obs(
        program="newbrand",
        parser="newbrand_referral_fr",
        canonical_fields={"referee_reward": "5 €"},
        observed_fields={"referee_reward": "10 €"},
        changes=[FieldChange(field="referee_reward", old="5 €", new="10 €")],
    )
    result = evaluate_all([obs], store=store)
    assert result["scope"] == "ALL_VERIFIED"
    assert [r["program"] for r in result["accepts"]] == ["newbrand"]


def test_effective_sync_routes_batches_nonpersonal_operator_values(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import lib.inventory as inventory
    import lib.renderer as renderer

    store = OperatorOverrideStore(path=tmp_path / "ov.json")
    store.upsert("igraal", "referee_reward", "3 €")
    store.upsert("igraal", "personal_link", "https://example.test/new")

    ref = SimpleNamespace(
        platform="code-parrainage",
        program="igraal",
        language="fr",
    )
    mapping = SimpleNamespace(
        platform="code-parrainage",
        program="igraal",
        language="fr",
        mutable_fields=["personal_link", "referee_reward"],
        platform_values={
            "personal_link": "https://example.test/old",
            "referee_reward": "5 €",
        },
    )

    monkeypatch.setattr(inventory, "list_mapping_refs", lambda: [ref])
    monkeypatch.setattr(
        renderer,
        "MappingRepository",
        lambda: SimpleNamespace(load=lambda *_a, **_k: mapping),
    )
    monkeypatch.setattr(
        "lib.monitor.auto_accept.load_accepted_monitor_fields",
        lambda: {},
    )

    routes = effective_sync_routes(store=store)
    diffs = routes["safe_diffs"]
    assert len(diffs) == 1
    assert diffs[0]["program"] == "igraal"
    assert set(diffs[0]["changed_fields"]) == {"referee_reward"}
    assert diffs[0]["changed_fields"]["referee_reward"]["new"] == "3 €"
