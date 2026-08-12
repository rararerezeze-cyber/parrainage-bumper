"""Deterministic HTML reward/conditions extraction (discovery / REVIEW fallback).

Never treat generic HIGH as final proof of a reliable program parser.
"""
from __future__ import annotations

import hashlib
import re
from html import unescape

from lib.monitor.models import Confidence, FailureCode, NormalizedOffer, SourceConfig
from lib.monitor.normalize import is_plausible_reward, normalize_field, normalize_reward


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>|</div>|</li>|</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


REWARD_PATTERNS = [
    re.compile(
        r"(?i)(?:bonus|récompense|recompense|jusqu['’]à|jusqu a|offre de|gagnez|recevez|offert[es]?)\s*[:\-]?\s*"
        r"([^\n.]{0,20}?\d[\d\s.,]*\s*€[^\n.]{0,60})"
    ),
    re.compile(r"(?i)(\d[\d\s.,]*\s*€\s*(?:en\s+\w+|offerts?|de\s+bonus|remboursés?)[^\n.]{0,40})"),
    re.compile(r"(?i)(jusqu['’]à\s+\d[\d\s.,]*\s*€[^\n.]{0,50})"),
    re.compile(r"(?i)(\d+\s*%\s*(?:de\s+)?(?:réduction|reduction|frais|cashback)[^\n.]{0,40})"),
]


def _extract_side_fields(text: str) -> dict[str, str | None]:
    """Multi-field model: do not guess which euro amount is the reward alone."""
    fields: dict[str, str | None] = {}
    m = re.search(
        r"(?i)(?:dépôt|depot|deposit|versement)\s*(?:minimum|min\.?)?\s*(?:de\s*)?(\d[\d\s.,]*\s*€)",
        text,
    )
    if m:
        fields["min_deposit"] = normalize_reward(m.group(1))
    m = re.search(
        r"(?i)(?:achat|spend|dépense|depense)\s*(?:minimum|min\.?)?\s*(?:de\s*)?(\d[\d\s.,]*\s*€)",
        text,
    )
    if m:
        fields["min_spend"] = normalize_reward(m.group(1))
    m = re.search(
        r"(?i)(?:trade|échange|echange|trading)\s*(?:minimum|min\.?)?\s*(?:de\s*)?(\d[\d\s.,]*\s*€)",
        text,
    )
    if m:
        fields["trade_min"] = normalize_reward(m.group(1))
    m = re.search(r"(?i)(?:dans les|sous|within)\s*(\d+)\s*jours?", text)
    if m:
        fields["qualification_days"] = m.group(1)
    m = re.search(r"(?i)(\d+)\s*jours?\s*(?:pour|afin de|to)\s*(?:qualifier|valider|activer)", text)
    if m:
        fields["qualification_days"] = m.group(1)
    m = re.search(r"(?i)(?:expire|valable jusqu|valid until|deadline)[^\d]{0,20}(\d{1,2}[/\.-]\d{1,2}[/\.-]\d{2,4})", text)
    if m:
        fields["expiry_date"] = m.group(1)
    m = re.search(r"(?i)(\d+)\s*(?:transactions?|achats?|trades?)", text)
    if m and int(m.group(1)) <= 50:
        fields["transaction_count"] = m.group(1)
    return fields


def parse_generic_reward_html(
    html: str,
    cfg: SourceConfig,
    offer: dict | None = None,
) -> NormalizedOffer:
    notes: list[str] = []
    conf = Confidence.REVIEW
    failure = FailureCode.NONE
    text = _html_to_text(html or "")
    raw_fp = hashlib.sha256((html or "")[:50000].encode("utf-8", errors="replace")).hexdigest()[:16]

    if not text or len(text) < 40:
        return NormalizedOffer(
            program=cfg.program,
            fields={},
            confidence=Confidence.REJECT,
            parser=cfg.parser,
            source_url=cfg.source_url,
            raw_fingerprint=raw_fp,
            notes=["empty_or_short_page"],
            failure_code=FailureCode.EMPTY_PAGE,
        )

    low = text.lower()
    if any(
        x in low
        for x in (
            "captcha",
            "access denied",
            "just a moment",
            "cf-browser",
            "enable javascript and cookies",
            "403 forbidden",
            "attention required",
            "checking your browser",
        )
    ):
        return NormalizedOffer(
            program=cfg.program,
            fields={},
            confidence=Confidence.REJECT,
            parser=cfg.parser,
            source_url=cfg.source_url,
            raw_fingerprint=raw_fp,
            notes=["challenge_or_error_page"],
            failure_code=FailureCode.CHALLENGE,
        )
    if any(x in low for x in ("404 not found", "page not found", "page introuvable", "n’existe pas", "n'existe pas")):
        return NormalizedOffer(
            program=cfg.program,
            fields={},
            confidence=Confidence.REJECT,
            parser=cfg.parser,
            source_url=cfg.source_url,
            raw_fingerprint=raw_fp,
            notes=["dead_or_missing_page"],
            failure_code=FailureCode.DEAD_URL,
        )

    side = _extract_side_fields(text)
    candidates: list[str] = []
    for pat in REWARD_PATTERNS:
        for m in pat.finditer(text):
            phrase = normalize_reward(m.group(1) if m.lastindex else m.group(0))
            if phrase and is_plausible_reward(phrase):
                candidates.append(phrase)

    uniq: list[str] = []
    for c in candidates:
        if c not in uniq:
            uniq.append(c)

    amount_tokens = re.findall(r"\d[\d\s.,]*\s*€", text)
    amount_norm: list[str] = []
    for a in amount_tokens:
        n = normalize_reward(a)
        if n and n not in amount_norm:
            amount_norm.append(n)

    fields: dict[str, str | None] = dict(side)

    # If page has multiple amounts and side fields explain them, don't invent single reward
    if len(amount_norm) > 1:
        notes.append(f"multiple_amounts={amount_norm[:6]}")
        # only assign reward if a clear reward phrase exists and differs from mins
        mins = {fields.get("min_deposit"), fields.get("min_spend"), fields.get("trade_min")}
        reward_cands = [u for u in uniq if u not in mins]
        if len(reward_cands) == 1:
            fields["referee_reward"] = reward_cands[0]
            conf = Confidence.REVIEW
            notes.append("multi_amount_reward_disambiguated_via_context")
        elif len(uniq) == 1 and uniq[0] not in mins:
            fields["referee_reward"] = uniq[0]
            conf = Confidence.REVIEW
            notes.append("multi_amount_single_reward_phrase")
        else:
            conf = Confidence.REVIEW
            failure = FailureCode.AMBIGUOUS_REWARD
            notes.append("multiple_reward_candidates_no_guess")
            if uniq:
                notes.append(f"reward_candidates={uniq[:5]}")
    elif not uniq and not amount_norm:
        conf = Confidence.REJECT
        failure = FailureCode.NO_PUBLIC_OFFER
        notes.append("no_reward_phrase_found")
    else:
        fields["referee_reward"] = uniq[0] if uniq else amount_norm[0]
        conf = Confidence.HIGH
        notes.append("single_reward_candidate")

    cond_m = re.search(
        r"(?i)(?:conditions?|éligibilité|eligibilite|offre réservée|offre reservee)[^\n]{10,160}",
        text,
    )
    if cond_m and "conditions" in (cfg.fields_supported or list(fields.keys()) + ["conditions"]):
        fields["conditions"] = normalize_field("conditions", cond_m.group(0)[:160])

    if offer and fields.get("referee_reward"):
        can = normalize_reward(str(offer.get("reward") or ""))
        obs = normalize_reward(fields["referee_reward"])
        if can and obs and can == obs:
            conf = Confidence.HIGH
            notes.append("matches_canonical_reward")
        elif can and obs and (can in obs or obs in can):
            if conf != Confidence.REJECT:
                conf = Confidence.HIGH
            notes.append("partial_match_canonical_reward")

    fields = {k: v for k, v in fields.items() if v}

    return NormalizedOffer(
        program=cfg.program,
        fields=fields,
        confidence=conf,
        parser=cfg.parser,
        source_url=cfg.source_url,
        raw_fingerprint=raw_fp,
        notes=notes,
        failure_code=failure,
        extraction_mode="html",
    )
