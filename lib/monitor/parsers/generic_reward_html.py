"""Deterministic HTML reward/conditions extraction (no LLM)."""
from __future__ import annotations

import hashlib
import re
from html import unescape

from lib.monitor.models import Confidence, NormalizedOffer, SourceConfig
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


def parse_generic_reward_html(
    html: str,
    cfg: SourceConfig,
    offer: dict | None = None,
) -> NormalizedOffer:
    notes: list[str] = []
    conf = Confidence.REVIEW
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
        )

    # challenge / error heuristics
    low = text.lower()
    if any(
        x in low
        for x in (
            "captcha",
            "access denied",
            "just a moment",
            "cf-browser",
            "enable javascript",
            "403 forbidden",
            "404 not found",
            "page not found",
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
        )

    candidates: list[str] = []
    for pat in REWARD_PATTERNS:
        for m in pat.finditer(text):
            phrase = normalize_reward(m.group(1) if m.lastindex else m.group(0))
            if phrase and is_plausible_reward(phrase):
                candidates.append(phrase)

    # unique preserve order
    uniq: list[str] = []
    for c in candidates:
        if c not in uniq:
            uniq.append(c)

    # Distinct euro amounts on page → always REVIEW if >1
    amount_tokens = re.findall(r"\d[\d\s.,]*\s*€", text)
    amount_norm = []
    for a in amount_tokens:
        n = normalize_reward(a)
        if n and n not in amount_norm:
            amount_norm.append(n)

    fields: dict[str, str | None] = {}
    if not uniq and not amount_norm:
        conf = Confidence.REJECT
        notes.append("no_reward_phrase_found")
    elif len(amount_norm) > 1 or len(uniq) > 1:
        fields["referee_reward"] = (uniq[0] if uniq else amount_norm[0])
        conf = Confidence.REVIEW
        notes.append(f"multiple_reward_candidates={(uniq or amount_norm)[:5]}")
    else:
        fields["referee_reward"] = uniq[0] if uniq else amount_norm[0]
        conf = Confidence.HIGH
        notes.append("single_reward_candidate")

    # conditions snippet if present
    cond_m = re.search(
        r"(?i)(?:conditions?|éligibilité|eligibilite|offre réservée|offre reservee)[^\n]{10,160}",
        text,
    )
    if cond_m and "conditions" in (cfg.fields_supported or ["referee_reward", "conditions"]):
        fields["conditions"] = normalize_field("conditions", cond_m.group(0)[:160])

    # Prefer matching against known canonical reward for confidence boost
    if offer and fields.get("referee_reward"):
        can = normalize_reward(str(offer.get("reward") or ""))
        obs = normalize_reward(fields["referee_reward"])
        if can and obs and can == obs:
            conf = Confidence.HIGH
            notes.append("matches_canonical_reward")
        elif can and obs and can in obs or (obs and can and obs in can):
            conf = Confidence.HIGH if conf != Confidence.REJECT else conf
            notes.append("partial_match_canonical_reward")

    # Never invent empty wipe
    fields = {k: v for k, v in fields.items() if v}

    return NormalizedOffer(
        program=cfg.program,
        fields=fields,
        confidence=conf,
        parser=cfg.parser,
        source_url=cfg.source_url,
        raw_fingerprint=raw_fp,
        notes=notes,
    )
