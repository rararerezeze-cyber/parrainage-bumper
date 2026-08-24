"""Every verb AGENTS.md documents must actually parse in the backend.

AGENTS.md is what Hermes loads to know how to route a Telegram message here.
A verb documented there but rejected by the parser is a silent contract break:
the operator types the documented phrase and gets a bare parse_error.
(Real gap found 2026-08-24: `expiration`, `type de récompense`,
`nombre de transactions` and `minimum de trade` were all documented and all
rejected.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.operator_overrides import FIELD_ALIASES, normalize_field_name

ROOT = Path(__file__).resolve().parents[1]

# The exact phrasings AGENTS.md lists under "<verb>".
DOCUMENTED_SET_VERBS = {
    "code": "personal_code",
    "lien": "personal_link",
    "link": "personal_link",
    "gain filleul": "referee_reward",
    "récompense filleul": "referee_reward",
    "gain parrain": "referrer_reward",
    "récompense parrain": "referrer_reward",
    "conditions": "conditions",
    "dépôt minimum": "min_deposit",
    "dépense minimum": "min_spend",
    "minimum de trade": "trade_min",
    "nombre de transactions": "transaction_count",
    "délai": "qualification_days",
    "expiration": "expiry_date",
    "type de récompense": "reward_type",
    "titre": "title",
}


@pytest.mark.parametrize("phrase,expected", sorted(DOCUMENTED_SET_VERBS.items()))
def test_documented_verb_resolves_to_its_backend_field(phrase, expected):
    assert normalize_field_name(phrase) == expected, phrase


@pytest.mark.parametrize("phrase", sorted(DOCUMENTED_SET_VERBS))
def test_documented_verb_appears_in_agents_md(phrase):
    """Guards the other direction: the doc and this table stay in step."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert phrase in agents, phrase


def test_no_alias_maps_to_an_unknown_field():
    from lib.operator_overrides import PILOTABLE_FIELDS

    for phrase, field in FIELD_ALIASES.items():
        assert field in PILOTABLE_FIELDS, f"{phrase} → {field}"
