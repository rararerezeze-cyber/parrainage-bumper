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
        "source_excerpt": "Le parrainage offre 10 € au filleul inscrit.",
        "source_url": "https://www.example.test/parrainage",
    }
    obs = _obs(
        program="newbrand",
        parser="newbrand_referral_fr",
        canonical_fields={"referee_reward": "5 €"},
        observed_fields={"referee_reward": "10 €"},
        changes=[FieldChange(field="referee_reward", old="5 €", new="10 €")],
        evidence_excerpts={"referee_reward": "Le parrainage offre 10 € au filleul inscrit."},
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
        evidence_excerpts={"referee_reward": "Le parrainage offre 10 € au filleul inscrit."},
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



def test_scheduled_monitor_does_not_retry_old_operator_writes_without_new_candidate(monkeypatch):
    import lib.monitor.auto_accept as module

    monkeypatch.setattr(module, "auto_accept_enabled", lambda: True)
    monkeypatch.setattr(module, "evaluate_all", lambda _observations: {
        "accepts": [], "rejects": [{"decision": "REJECT"}], "scope": "ALL_VERIFIED",
    })

    def forbidden(*_args, **_kwargs):
        raise AssertionError("No verified new candidate: must not enqueue, sync, or write")

    monkeypatch.setattr(module, "apply_accepts", forbidden)
    monkeypatch.setattr(module, "effective_sync_routes", forbidden)
    monkeypatch.setattr(module, "execute_safe_sync_routes", forbidden)

    result = module.apply_verified_batch(
        [], run_writers=True, require_new_verified_candidate=True,
    )
    assert result["ok"] is True
    assert result["applied"] is False
    assert result["reason"] == "no_verified_candidate"
    assert result["verified_writes"] == result["failed_writes"] == 0
    assert result["rejected_candidates"] == 1


def test_monitor_report_restores_literal_evidence_for_verified_candidates(tmp_path):
    from lib.monitor.auto_accept import observations_from_last_report

    original = _obs(
        program="newbrand",
        observed_fields={"referee_reward": "10 €"},
        changes=[FieldChange(field="referee_reward", old="5 €", new="10 €")],
        evidence_excerpts={"referee_reward": "Le filleul reçoit 10 € de bonus de parrainage."},
    )
    path = tmp_path / "monitor-report.json"
    path.write_text(json.dumps({"observations": [original.to_dict()]}), encoding="utf-8")
    restored = observations_from_last_report(path)
    assert len(restored) == 1
    assert restored[0].evidence_excerpts == original.evidence_excerpts
    assert restored[0].changes[0].new == "10 €"



def test_scheduled_verified_change_never_retries_unrelated_operator_override(monkeypatch):
    import lib.monitor.auto_accept as module

    desired = {"program": "newbrand", "field": "referee_reward", "observed": "10 €"}
    monkeypatch.setattr(module, "auto_accept_enabled", lambda: True)
    monkeypatch.setattr(module, "evaluate_all", lambda _observations: {
        "accepts": [desired], "rejects": [], "scope": "ALL_VERIFIED",
    })
    flags = []

    def accepted(rows, *, force=False, pending_only_for_applied=False):
        flags.append((force, pending_only_for_applied))
        return {"count": 1, "applied_rows": [desired]}

    monkeypatch.setattr(module, "apply_accepts", accepted)
    monkeypatch.setattr(module, "effective_sync_routes", lambda: {
        "safe_diffs": [
            {
                "platform": "parrainage-co", "program": "totalenergies", "language": "fr",
                "route": "AUTO_ON_SAFE_DIFF",
                "changed_fields": {"referee_reward": {"new": "50 €"}},
            },
            {
                "platform": "parrainage-co", "program": "newbrand", "language": "fr",
                "route": "AUTO_ON_SAFE_DIFF",
                "changed_fields": {
                    "referee_reward": {"new": "10 €"},
                    "referrer_reward": {"new": "5 €"},
                },
            },
        ],
        "auto_writers": ["parrainage-co"],
    })
    executed = []

    def writer(routes):
        executed.extend(routes["safe_diffs"])
        return []

    monkeypatch.setattr(module, "execute_safe_sync_routes", writer)
    report = module.apply_verified_batch(
        [], run_writers=True, require_new_verified_candidate=True,
    )
    assert report["ok"] is True
    assert flags == [(True, True)]
    assert len(executed) == 1
    assert executed[0]["program"] == "newbrand"
    assert set(executed[0]["changed_fields"]) == {"referee_reward"}
    assert report["verified_writes"] == 0


def test_scheduled_new_accept_does_not_requeue_unrelated_super_pending(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import lib.monitor.auto_accept as module
    import lib.safety as safety
    import lib.super_parrain_schedule as super_schedule

    monkeypatch.setattr(module, "auto_accept_enabled", lambda: True)
    monkeypatch.setattr(safety, "snapshot_state", lambda *_a, **_k: {"id": "test"})
    monkeypatch.setattr(module, "load_accepted_monitor_fields", lambda: {})
    monkeypatch.setattr(module, "save_accepted_fields", lambda *_a: tmp_path / "accepted.json")
    monkeypatch.setattr(module, "HISTORY_PATH", tmp_path / "accepted-history.jsonl")
    monkeypatch.setattr(module, "effective_sync_routes", lambda: {
        "safe_diffs": [
            {"platform": "super-parrain", "program": "totalenergies",
             "changed_fields": {"referee_reward": {"new": "50 €"}}},
            {"platform": "super-parrain", "program": "newbrand",
             "changed_fields": {"referee_reward": {"new": "10 €"}}},
        ]
    })
    queued = []
    monkeypatch.setattr(super_schedule, "enqueue_pending", lambda *args, **kwargs: queued.append((args, kwargs)))
    result = module.apply_accepts(
        [{"program": "newbrand", "field": "referee_reward", "observed": "10 €"}],
        force=True, pending_only_for_applied=True,
    )
    assert result["count"] == 1
    assert len(queued) == 1
    assert queued[0][0][1] == "newbrand"
    assert not any("totalenergies" in str(x) for x in queued)
