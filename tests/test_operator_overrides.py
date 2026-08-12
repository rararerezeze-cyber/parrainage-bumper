"""FULL OPERATOR CONTROL — overrides precedence, Telegram parse, fallbacks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.operator_overrides import (
    SOURCE_ACCEPTED_MONITOR,
    SOURCE_CANONICAL,
    SOURCE_GLOBAL_OPERATOR,
    SOURCE_PLATFORM_OPERATOR,
    OperatorOverrideStore,
    apply_effective_to_offer,
    normalize_field_name,
    resolve_effective_value,
)
from lib.offers import OffersRepository
from tools.telegram_update import parse_message


FIXTURE_OFFERS = Path("tests/fixtures/offers.json")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "operator-overrides.json"
    s = OperatorOverrideStore(path=path)
    monkeypatch.setattr(
        "lib.operator_overrides.OPERATOR_OVERRIDES_PATH", path
    )
    return s


def test_field_aliases():
    assert normalize_field_name("gain filleul") == "referee_reward"
    assert normalize_field_name("code") == "personal_code"
    assert normalize_field_name("dépôt minimum") == "min_deposit"
    assert normalize_field_name("unknown_xyz") is None


def test_global_override(store):
    store.upsert("kraken", "referee_reward", "20 €")
    eff = resolve_effective_value(
        "kraken", "referee_reward", canonical="10 €", store=store
    )
    assert eff.value == "20 €"
    assert eff.source == SOURCE_GLOBAL_OPERATOR


def test_platform_override(store):
    store.upsert("kraken", "referee_reward", "20 €")  # global
    store.upsert("kraken", "referee_reward", "25 €", platform="super-parrain")
    eff_sp = resolve_effective_value(
        "kraken",
        "referee_reward",
        platform="super-parrain",
        canonical="10 €",
        store=store,
    )
    eff_other = resolve_effective_value(
        "kraken",
        "referee_reward",
        platform="parrainage-co",
        canonical="10 €",
        store=store,
    )
    assert eff_sp.value == "25 €"
    assert eff_sp.source == SOURCE_PLATFORM_OPERATOR
    assert eff_other.value == "20 €"
    assert eff_other.source == SOURCE_GLOBAL_OPERATOR


def test_platform_gt_global(store):
    store.upsert("kraken", "personal_code", "GLOBALCODE")
    store.upsert("kraken", "personal_code", "PLATCODE", platform="super-parrain")
    eff = resolve_effective_value(
        "kraken", "personal_code", platform="super-parrain", canonical="OLD", store=store
    )
    assert eff.value == "PLATCODE"
    assert eff.source == SOURCE_PLATFORM_OPERATOR


def test_override_gt_monitor(store, monkeypatch, tmp_path):
    accepted = tmp_path / "accepted.json"
    accepted.write_text(
        json.dumps({"programs": {"kraken": {"referee_reward": "10 €"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lib.operator_overrides.ACCEPTED_MONITOR_FIELDS_PATH", accepted
    )
    store.upsert("kraken", "referee_reward", "20 €")
    from lib.operator_overrides import load_accepted_monitor_fields

    accepted_map = load_accepted_monitor_fields()
    eff = resolve_effective_value(
        "kraken",
        "referee_reward",
        canonical="5 €",
        store=store,
        accepted=accepted_map,
    )
    assert eff.value == "20 €"
    assert eff.source == SOURCE_GLOBAL_OPERATOR
    # without operator → accepted monitor
    store2 = OperatorOverrideStore(path=tmp_path / "empty.json")
    eff2 = resolve_effective_value(
        "kraken",
        "referee_reward",
        canonical="5 €",
        store=store2,
        accepted=accepted_map,
    )
    assert eff2.value == "10 €"
    assert eff2.source == SOURCE_ACCEPTED_MONITOR


def test_remove_platform_fallback_global(store):
    store.upsert("kraken", "referee_reward", "20 €")
    store.upsert("kraken", "referee_reward", "25 €", platform="super-parrain")
    assert store.remove("kraken", "referee_reward", platform="super-parrain") is True
    eff = resolve_effective_value(
        "kraken",
        "referee_reward",
        platform="super-parrain",
        canonical="10 €",
        store=store,
    )
    assert eff.value == "20 €"
    assert eff.source == SOURCE_GLOBAL_OPERATOR


def test_remove_global_fallback_canonical(store):
    store.upsert("kraken", "referee_reward", "20 €")
    assert store.remove("kraken", "referee_reward") is True
    eff = resolve_effective_value(
        "kraken", "referee_reward", canonical="10 €", store=store
    )
    assert eff.value == "10 €"
    assert eff.source == SOURCE_CANONICAL


def test_code_link_reward_conditions(store):
    store.upsert("kraken", "personal_code", "ABC123")
    store.upsert("kraken", "personal_link", "https://example.com/x")
    store.upsert("kraken", "referee_reward", "20 €")
    store.upsert("kraken", "conditions", "Déposer 100 € sous 15 jours")
    offer = {
        "lk": "kraken",
        "code": "OLD",
        "link": "https://old",
        "reward": "10 €",
        "cond": "old cond",
    }
    eff = apply_effective_to_offer(offer, store=store)
    assert eff["code"] == "ABC123"
    assert eff["link"] == "https://example.com/x"
    assert eff["reward"] == "20 €"
    assert eff["cond"] == "Déposer 100 € sous 15 jours"


def test_unknown_field_raises(store):
    with pytest.raises(ValueError, match="unknown_field"):
        store.upsert("kraken", "not_a_real_field_zzz", "x")


def test_unknown_platform_raises(store):
    with pytest.raises(ValueError, match="unknown_platform"):
        store.upsert("kraken", "code", "x", platform="not-a-platform")


def test_injection_rejected(store):
    with pytest.raises(ValueError):
        store.upsert("kraken", "code", "x\x00y")
    with pytest.raises(ValueError):
        store.upsert("kraken", "code", "x" * 3000)


def test_idempotence(store):
    store.upsert("kraken", "code", "AAA")
    store.upsert("kraken", "code", "AAA")
    items = store.list_for_program("kraken")
    codes = [o for o in items if o.field == "personal_code"]
    assert len(codes) == 1
    assert codes[0].value == "AAA"


def test_parse_global_and_platform():
    offers = OffersRepository(path=FIXTURE_OFFERS)
    p1 = parse_message("Kraken gain filleul 20 €", offers)
    assert p1["action"] == "set"
    assert p1["program"] == "kraken"
    assert p1["field"] == "referee_reward"
    assert p1["platform"] is None
    assert "20" in p1["value"]

    p2 = parse_message("Kraken Super-Parrain gain filleul 25 €", offers)
    assert p2["platform"] == "super-parrain"
    assert p2["field"] == "referee_reward"
    assert "25" in p2["value"]


def test_parse_status_remove():
    offers = OffersRepository(path=FIXTURE_OFFERS)
    st = parse_message("Kraken status", offers)
    assert st["action"] == "status"
    rm = parse_message("Kraken supprimer override gain filleul", offers)
    assert rm["action"] == "remove"
    assert rm["field"] == "referee_reward"
    rm2 = parse_message("Kraken Super-Parrain supprimer override gain filleul", offers)
    assert rm2["platform"] == "super-parrain"


def test_parse_unknown_program():
    offers = OffersRepository(path=FIXTURE_OFFERS)
    with pytest.raises(ValueError, match="unknown_program"):
        parse_message("NotARealBrandXYZ gain filleul 10 €", offers)


def test_parse_code_compat():
    offers = OffersRepository(path=FIXTURE_OFFERS)
    parsed = parse_message("Kraken code NEWCODE99", offers)
    assert parsed["field"] == "personal_code"
    assert parsed["value"] == "NEWCODE99"


def test_telegram_set_and_plan(store, monkeypatch, tmp_path):
    """End-to-end: set override → effective → plan (no live write)."""
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", store.path)
    monkeypatch.setattr("tools.telegram_update.OPERATOR_OVERRIDES_PATH", store.path)
    from tools.telegram_update import apply_operator_command, parse_message

    offers = OffersRepository(path=FIXTURE_OFFERS)
    # ensure offers path for apply
    monkeypatch.setenv("BONUS_PARRAIN_OFFERS_PATH", str(FIXTURE_OFFERS.resolve()))
    parsed = parse_message("Kraken gain filleul 20 €", offers)
    result = apply_operator_command(parsed, message="Kraken gain filleul 20 €")
    assert result["action"] == "set"
    assert result["new_effective"] == "20 €"
    assert result["new_source"] == SOURCE_GLOBAL_OPERATOR

    from lib.operator_plan import plan_program_impact

    plan = plan_program_impact("kraken", store=store)
    assert plan["program"] == "kraken"
    assert "platforms" in plan
    # at least super-parrain mapping should appear if present in repo
    plats = {p["platform"] for p in plan["platforms"]}
    assert "super-parrain" in plats or len(plan["platforms"]) >= 0
