"""Fail-closed Super-Parrain resource classification.

Codes-promo edit is the historical bump form. Content writes must target
Mes annonces only. Never fill a field before the resource type is proven.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

HARD_STOP_WRONG_RESOURCE = "HARD_STOP_WRONG_RESOURCE"
CODES_PROMO_PATH = "/codes-promo/"
CODES_PROMO_FORM = "edit_code_promo_by_user_form"

# Discovered Mes annonces edit routes (extended after READ-ONLY probe).
ANNOUNCEMENT_EDIT_WHITELIST = (
    "/tableau-de-bord/annonces/",
    "/tableau-de-bord/mes-annonces/",
    "/tableau-de-bord/parrainages/",
)

FORBIDDEN_EDIT_PATHS = (
    CODES_PROMO_PATH,
    "/codes-promo",
)

POULPEO_PUBLIC = (
    "https://www.super-parrain.com/offres/poulpeo/parrainage-poulpeo/annonces/adrien-b-8"
)
POULPEO_CODE = "4KD2ab"
POULPEO_SPONSOR = "sponsor_key=4KD2ab"


def classify_edit_url(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return "UNKNOWN"
    path = (urlparse(raw).path or raw).lower()
    if CODES_PROMO_PATH in path or path.rstrip("/").endswith("/codes-promo"):
        return "CODES_PROMO"
    if any(w in path for w in ANNOUNCEMENT_EDIT_WHITELIST) and (
        "/edit" in path or "modifier" in path
    ):
        return "ANNOUNCEMENT"
    if "/edit" in path and "annonce" in path and CODES_PROMO_PATH not in path:
        return "ANNOUNCEMENT"
    return "UNKNOWN"


def assert_announcement_edit_url(url: str | None) -> str:
    """Raise HARD_STOP_WRONG_RESOURCE unless url is a Mes annonces edit."""
    raw = (url or "").strip()
    if CODES_PROMO_PATH in raw.lower() or "/codes-promo/" in raw.lower():
        raise RuntimeError(f"{HARD_STOP_WRONG_RESOURCE}: codes-promo url={raw}")
    kind = classify_edit_url(raw)
    if kind != "ANNOUNCEMENT":
        raise RuntimeError(
            f"{HARD_STOP_WRONG_RESOURCE}: not Mes annonces edit ({kind}) url={raw}"
        )
    return raw


def form_is_codes_promo(form_names: list[str] | None, html_or_names: str | None = None) -> bool:
    blob = " ".join(form_names or [])
    if html_or_names:
        blob = f"{blob} {html_or_names}"
    return CODES_PROMO_FORM in blob.lower()


def assert_not_codes_promo_form(
    *,
    url: str | None = None,
    form_names: list[str] | None = None,
    html: str | None = None,
) -> None:
    if url and (CODES_PROMO_PATH in url.lower() or "/codes-promo/" in url.lower()):
        raise RuntimeError(f"{HARD_STOP_WRONG_RESOURCE}: codes-promo url={url}")
    if form_is_codes_promo(form_names, html):
        raise RuntimeError(
            f"{HARD_STOP_WRONG_RESOURCE}: {CODES_PROMO_FORM} present"
        )


def poulpeo_pre_save_assertions(
    *,
    page_url: str,
    page_text: str,
    public_listing: str,
    rendered: str,
    historical: str,
) -> dict[str, Any]:
    """Required checks before any future Enregistrer on Poulpeo."""
    errors: list[str] = []
    text = page_text or ""
    if classify_edit_url(page_url) != "ANNOUNCEMENT":
        errors.append(f"page_not_announcement:{classify_edit_url(page_url)}")
    if CODES_PROMO_PATH in (page_url or "").lower() or CODES_PROMO_FORM in text.lower():
        errors.append("codes_promo_present")
    if "adrien-b-8" not in (public_listing or "") and "adrien-b-8" not in text.lower():
        errors.append("public_listing_not_adrien-b-8")
    if "poulpeo" not in (page_url or "").lower() and "poulpeo" not in text.lower():
        errors.append("page_not_poulpeo")
    if POULPEO_CODE not in text:
        errors.append("code_4KD2ab_missing")
    if POULPEO_SPONSOR not in text and POULPEO_CODE not in text:
        errors.append("sponsor_key_missing")
    if text.count("5€") < 3 and historical.count("5€") < 3:
        errors.append("reward_5eur_occurrences_missing")
    if rendered.count("3€") != 3:
        errors.append("rendered_not_three_3eur")
    if rendered.count("5€") != 0:
        errors.append("rendered_still_has_5eur")
    if POULPEO_CODE not in rendered or POULPEO_SPONSOR not in rendered:
        errors.append("rendered_lost_identity")
    return {
        "ok": not errors,
        "errors": errors,
        "page_url": page_url,
        "public_listing": public_listing,
        "reward_5eur_on_page": text.count("5€"),
        "reward_3eur_rendered": rendered.count("3€"),
    }
