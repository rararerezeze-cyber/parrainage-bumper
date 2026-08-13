from lib.super_parrain_resource import (
    HARD_STOP_WRONG_RESOURCE,
    assert_announcement_edit_url,
    assert_not_codes_promo_form,
    classify_edit_url,
    poulpeo_pre_save_assertions,
)
import pytest


def test_codes_promo_url_is_hard_stop():
    url = (
        "https://www.super-parrain.com/tableau-de-bord/codes-promo/"
        "offre-parrainage-poulpeo-5eur-offerts-a-linscription/edit"
    )
    assert classify_edit_url(url) == "CODES_PROMO"
    with pytest.raises(RuntimeError, match=HARD_STOP_WRONG_RESOURCE):
        assert_announcement_edit_url(url)


def test_mes_annonces_edit_accepted():
    url = "https://www.super-parrain.com/tableau-de-bord/annonces/12345/edit"
    assert classify_edit_url(url) == "ANNOUNCEMENT"
    assert assert_announcement_edit_url(url) == url


def test_codes_promo_form_rejected():
    with pytest.raises(RuntimeError, match=HARD_STOP_WRONG_RESOURCE):
        assert_not_codes_promo_form(
            url="https://www.super-parrain.com/tableau-de-bord/annonces/1/edit",
            form_names=["edit_code_promo_by_user_form[description]"],
        )


def test_poulpeo_pre_save_happy():
    hist = "x 5€ y 5€ z 5€"
    rendered = "x 3€ y 3€ z 3€ code 4KD2ab sponsor_key=4KD2ab"
    chk = poulpeo_pre_save_assertions(
        page_url="https://www.super-parrain.com/tableau-de-bord/annonces/9/edit",
        page_text="Poulpeo 5€ 5€ 5€ 4KD2ab sponsor_key=4KD2ab adrien-b-8",
        public_listing="https://www.super-parrain.com/offres/poulpeo/parrainage-poulpeo/annonces/adrien-b-8",
        rendered=rendered,
        historical=hist,
    )
    assert chk["ok"] is True


def test_poulpeo_pre_save_rejects_codes_promo_page():
    chk = poulpeo_pre_save_assertions(
        page_url="https://www.super-parrain.com/tableau-de-bord/codes-promo/poulpeo/edit",
        page_text="edit_code_promo_by_user_form",
        public_listing="https://example",
        rendered="3€ 3€ 3€ 4KD2ab sponsor_key=4KD2ab",
        historical="5€ 5€ 5€",
    )
    assert chk["ok"] is False
    assert any("codes_promo" in e or "announcement" in e for e in chk["errors"])
