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
def renderer(tmp_path, monkeypatch) -> Renderer:
    ov = tmp_path / "operator-overrides.json"
    ov.write_text('{"version":1,"overrides":[]}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    return Renderer(OffersRepository(path=FIXTURES / "offers.json"))


def test_untrusted_link_is_literal_text(renderer, kraken_mapping, kraken_template):
    rendered = renderer.render(
        kraken_template,
        kraken_mapping,
        overrides={"personal_link": "javascript:alert(1)"},
    )
    assert "javascript:alert(1)" in rendered
    assert "<script>" not in rendered
