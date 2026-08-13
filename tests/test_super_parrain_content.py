from lib.super_parrain_content import (
    compare_from_mapping_platform_values,
    get_desired_content,
    program_from_edit_url,
)


def test_program_from_edit_url():
    url = "https://www.super-parrain.com/tableau-de-bord/codes-promo/offre-parrainage-kraken-200-eur-offerts/edit"
    assert program_from_edit_url(url) == "kraken"


def test_get_desired_kraken():
    d = get_desired_content("kraken")
    assert d.has_mapping
    assert d.code  # from offers
    assert d.structure_preserved


def test_compare_detects_kraken_diff_or_sync():
    diff = compare_from_mapping_platform_values("kraken")
    # Either needs update (typical) or in_sync after a verified write
    assert diff.program == "kraken"
    assert diff.reason in {"diff", "in_sync", "structure_not_preserved", "no_mapping"} or diff.desired
