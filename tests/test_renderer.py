import json
from pathlib import Path

import pytest

from lib.models import PlatformMapping
from lib.offers import OffersRepository
from lib.renderer import Renderer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def kraken_mapping() -> PlatformMapping:
    with (FIXTURES / "super-parrain" / "kraken.fr.mapping.json").open(encoding="utf-8") as fh:
        return PlatformMapping.from_dict(json.load(fh))


@pytest.fixture
def kraken_template() -> str:
    return (FIXTURES / "super-parrain" / "kraken.fr.txt").read_text(encoding="utf-8")


@pytest.fixture
def renderer() -> Renderer:
    return Renderer(OffersRepository(path=FIXTURES / "offers.json"))


def test_render_matches_golden_template(renderer, kraken_mapping, kraken_template):
    offer = renderer.offers.get_by_slug("kraken")
    golden = (FIXTURES / "super-parrain" / "kraken.fr.golden.txt").read_text(encoding="utf-8")
    rendered = renderer.render(kraken_template, kraken_mapping, offer=offer)
    assert rendered == golden


def test_render_code_change_only(renderer, kraken_mapping, kraken_template):
    rendered = renderer.render(
        kraken_template,
        kraken_mapping,
        overrides={"personal_code": "NEWCODE1"},
    )
    assert "NEWCODE1" in rendered
    assert "4hpz4gdy" not in rendered
    assert "proinvite.kraken.com" in rendered
    assert "🔥" in rendered


def test_missing_mutable_value_raises(renderer, kraken_mapping, kraken_template):
    offer = renderer.offers.get_by_slug("kraken").copy()
    offer["code"] = None
    with pytest.raises(ValueError, match="Valeur manquante"):
        renderer.render(kraken_template, kraken_mapping, offer=offer)


def test_non_mutable_marker_left_untouched(renderer, kraken_mapping):
    template = "Bonus parrain {{REFERRER_REWARD}} reste intact"
    rendered = renderer.render(template, kraken_mapping)
    assert "{{REFERRER_REWARD}}" in rendered
