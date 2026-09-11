from pathlib import Path

from lib.offers import OffersRepository
from tools.telegram_update import parse_message


def test_parse_kraken_code():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    parsed = parse_message("Kraken code NEWCODE99", offers)
    assert parsed["program"] == "kraken"
    assert parsed["field"] == "personal_code"
    assert parsed["value"] == "NEWCODE99"
    assert parsed["action"] == "set"


def test_parse_natural_french():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    parsed = parse_message("Le nouveau code Kraken est XYZ123", offers)
    assert parsed["program"] == "kraken"
    assert parsed["value"] == "XYZ123"
    assert parsed["field"] == "personal_code"


def test_parse_natural_status_and_values_aliases():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    assert parse_message("Kraken état", offers)["action"] == "status"
    assert parse_message("Kraken valeurs", offers)["action"] == "list"
    assert parse_message("Kraken modifications", offers)["action"] == "list"


def test_parse_natural_remove_without_override_keyword():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    parsed = parse_message("Kraken supprimer gain filleul", offers)
    assert parsed["action"] == "remove"
    assert parsed["field"] == "referee_reward"
    assert parsed["platform"] is None

    parsed2 = parse_message("Kraken retirer override gain filleul", offers)
    assert parsed2["action"] == "remove"
    assert parsed2["field"] == "referee_reward"


def test_parse_platform_name_variants():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    variants = [
        "Kraken Parrainage.co code ABC123",
        "Kraken Parrainage co code ABC123",
        "Kraken Parrainage-co code ABC123",
    ]
    for command in variants:
        parsed = parse_message(command, offers)
        assert parsed["platform"] == "parrainage-co"
        assert parsed["field"] == "personal_code"

    parsed = parse_message("Kraken ReferralCode tv code ABC123", offers)
    assert parsed["platform"] == "referralcode-tv"
    assert parsed["field"] == "personal_code"


def test_parse_platform_scoped_delete_natural_form():
    offers = OffersRepository(path=Path("tests/fixtures/offers.json"))
    parsed = parse_message("Kraken Super-Parrain effacer gain filleul", offers)
    assert parsed["action"] == "remove"
    assert parsed["platform"] == "super-parrain"
    assert parsed["field"] == "referee_reward"
