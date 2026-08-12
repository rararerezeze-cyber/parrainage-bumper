import json
from pathlib import Path

import pytest

from lib.models import PlatformMapping
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, TemplateRepository
from platforms.super_parrain.adapter import SuperParrainAdapter

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureMappingRepository(MappingRepository):
    def load(self, platform: str, program: str, language: str) -> PlatformMapping:
        path = FIXTURES / "super-parrain" / f"{program}.{language}.mapping.json"
        with path.open(encoding="utf-8") as fh:
            return PlatformMapping.from_dict(json.load(fh))


class FixtureTemplateRepository(TemplateRepository):
    def load_text(self, platform: str, program: str, language: str) -> str:
        return (FIXTURES / platform / f"{program}.{language}.txt").read_text(encoding="utf-8")

    def load_golden(self, platform: str, program: str, language: str) -> str:
        return (FIXTURES / platform / f"{program}.{language}.golden.txt").read_text(encoding="utf-8")

    def exists(self, platform: str, program: str, language: str) -> bool:
        return (FIXTURES / platform / f"{program}.{language}.txt").exists()

    def golden_exists(self, platform: str, program: str, language: str) -> bool:
        return (FIXTURES / platform / f"{program}.{language}.golden.txt").exists()


def test_dry_run_success_with_fixtures(tmp_path):
    adapter = SuperParrainAdapter(
        mappings=FixtureMappingRepository(),
        templates=FixtureTemplateRepository(),
        offers=OffersRepository(path=FIXTURES / "offers.json"),
    )
    mapping = FixtureMappingRepository().load("super-parrain", "kraken", "fr")
    result = adapter.dry_run(mapping)
    assert result.blocking is False
    assert result.golden_match is True
    assert result.status == "ready"


def test_dry_run_prod_golden_with_capture_values():
    """Property: template + valeurs capturées Super-Parrain == golden exact."""
    from lib.renderer import Renderer, MappingRepository, TemplateRepository

    mapping = MappingRepository().load("super-parrain", "kraken", "fr")
    templates = TemplateRepository()
    golden = templates.load_golden("super-parrain", "kraken", "fr")
    template = templates.load_text("super-parrain", "kraken", "fr")
    capture_offer = {
        "lk": "kraken",
        "code": "cpbrgddy",
        "link": "https://invite.kraken.com/JDNW/s5qudqe4",
        "reward": "200 € en cryptomonnaies",
    }
    renderer = Renderer(OffersRepository(path=FIXTURES / "offers.json"))
    rendered = renderer.render(template, mapping, offer=capture_offer)
    assert rendered == golden
    assert mapping.template_status == "ready"


def test_dry_run_prod_detects_offers_json_drift():
    """offers.json BonusParrain peut différer du texte Super-Parrain publié."""
    adapter = SuperParrainAdapter(
        mappings=MappingRepository(),
        templates=TemplateRepository(),
        offers=OffersRepository(),  # data/offers.json prod
    )
    mapping = MappingRepository().load("super-parrain", "kraken", "fr")
    result = adapter.dry_run(mapping)
    # Les valeurs BonusParrain actuelles ne reproduisent pas le golden Super-Parrain.
    assert result.blocking is True
    assert result.status == "golden_mismatch"
    assert result.golden_match is False
