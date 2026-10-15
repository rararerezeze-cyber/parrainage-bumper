"""Source-backed evidence for public monitor changes.

An excerpt is contextual evidence of a claim on a public page, NOT proof
that an operator's own third-party listing has changed. Missing/ambiguous
excerpts must fail closed for automatic acceptance.
"""
from __future__ import annotations

import re
from html import unescape

_REWARD = {"referee_reward", "referrer_reward"}
_FIELD_TERMS = {
    "referee_reward": r"filleul|parrain|parrainage|recommand|récompense|recompense|prime|bonus|offert|invité|invite|ami|referral",
    "referrer_reward": r"parrain|parrainage|recommand|récompense|recompense|prime|bonus|invitez|referral",
    "min_deposit": r"dépôt|depot|versement|deposit|déposer|deposer",
    "min_spend": r"achat|dépense|depense|spend|purchase",
    "trade_min": r"trade|transaction|échange|echange|trading",
    "qualification_days": r"jours|days|délai|delai",
    "expiry_date": r"expiration|expir|valid|jusqu",
}
_TARIFF = re.compile(
    r"(?i)(?:\bforfait\b|\babonnement\b|\bprix\b|\btarif\b|"
    r"\bpar\s+mois\b|/\s*mois\b|€\s*/\s*mois\b|\bmensuel)",
)


def _visible_text(html: str) -> str:
    # This is only a source excerpt, not a trusted page interpretation.
    cleaned = re.sub(r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>", " ", html or "")
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return " ".join(unescape(cleaned).split())[:250_000]


def _match_value(text: str, observed: str):
    value = " ".join(str(observed).split())
    if not value or len(value) > 250:
        return None
    # Flexible spacing covers "10€" vs the normalized "10 €".
    expression = re.escape(value).replace(r"\ ", r"\s*")
    try:
        return re.search(expression, text, flags=re.I)
    except re.error:
        return None


def source_excerpt(html: str, field: str, observed: str | None) -> str | None:
    """Return a short, literal on-page excerpt with field-specific context.

    False positives are preferable to *withhold* from approval: a contextless
    price is not accepted as a referral reward.
    """
    if not observed:
        return None
    text = _visible_text(html)
    if not text:
        return None
    match = _match_value(text, str(observed))
    if not match:
        return None
    start, end = match.span()
    before = text[max(0, start - 75):start]
    after = text[end:min(len(text), end + 75)]
    context = before + text[start:end] + after
    term = _FIELD_TERMS.get(field)
    if term and not re.search(term, context, re.I):
        return None
    if field in _REWARD:
        local = text[max(0, start - 28):min(len(text), end + 28)]
        # A mobile-plan price is not a referral reward, even if the same
        # paragraph elsewhere mentions sponsoring someone.
        if _TARIFF.search(local):
            return None
    # Never interpolate HTML or supply an invented/explanatory quote.
    snippet = text[max(0, start - 90):min(len(text), end + 90)]
    return snippet[:220]


def evidence_reviewable(
    *,
    field: str,
    observed: str | None,
    excerpt: str | None,
    source_url: str | None,
    observations: int,
    source_class: str,
    confidence: str,
    parser_tests_passed: bool,
) -> bool:
    """Minimum generic proof gate before proposing or auto-accepting changes."""
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        return False
    if source_class != "VERIFIED_OFFICIAL" or confidence != "HIGH" or not parser_tests_passed:
        return False
    if observations < 3 or not excerpt or not observed:
        return False
    # Check that the excerpt still literally supports the proposed value.
    match = _match_value(str(excerpt), str(observed))
    if not match:
        return False
    if field in _REWARD:
        s, e = match.span()
        local = excerpt[max(0, s - 28):min(len(excerpt), e + 28)]
        if _TARIFF.search(local):
            return False
    return True
