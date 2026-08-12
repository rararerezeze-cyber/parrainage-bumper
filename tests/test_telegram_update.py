from tools.telegram_update import parse_message
from lib.offers import OffersRepository
from pathlib import Path


def test_parse_kraken_code():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    # fixture has kraken
    parsed = parse_message("Kraken code NEWCODE99", offers)
    assert parsed["program"] == "kraken"
    assert parsed["field"] == "code"
    assert parsed["value"] == "NEWCODE99"


def test_parse_natural_french():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    parsed = parse_message("Le nouveau code Kraken est XYZ123", offers)
    assert parsed["program"] == "kraken"
    assert parsed["value"] == "XYZ123"
