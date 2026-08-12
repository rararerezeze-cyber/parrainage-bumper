"""Normalize extracted business values for comparison (not HTML)."""
from __future__ import annotations

import re


def normalize_text(s: str | None) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    t = t.replace("\u202f", " ").replace("\xa0", " ")
    t = " ".join(t.split())
    return t


def normalize_reward(s: str | None) -> str | None:
    t = normalize_text(s)
    if not t:
        return None
    # collapse variants of euro
    t = re.sub(r"(?i)\s*euros?\b", " €", t)
    t = re.sub(r"(?i)\s*eur\b", " €", t)
    t = re.sub(r"\s*€\s*", " € ", t)
    t = " ".join(t.split())
    # strip trailing marketing fluff that doesn't change the business amount
    t = re.sub(
        r"(?i)\s*(offerts?|selon l['’]offre|selon la campagne|for new users|for freinds|new users only)\s*",
        " ",
        t,
    )
    t = " ".join(t.split()).strip(" -:")
    # compound rewards: 10 € bonus + 10 € freebets
    m_comp = re.match(
        r"(?i)^(\d[\d\s.,]*\s*€\s*(?:bonus|cash|espèces)?\s*(?:\+|et)\s*\d[\d\s.,]*\s*€\s*(?:freebets?|free bets?|bonus)?)",
        t,
    )
    if m_comp:
        return " ".join(m_comp.group(1).split())
    # keep core amount + asset if present
    m = re.match(
        r"(?i)^((?:jusqu['’]à\s+)?\d[\d\s.,]*\s*€(?:\s+en\s+\w+)?)",
        t,
    )
    if m:
        return m.group(1).strip()
    return t or None


def normalize_field(name: str, value: str | None) -> str | None:
    if name in {"referee_reward", "referrer_reward"}:
        return normalize_reward(value)
    return normalize_text(value)


def amounts_in_text(s: str | None) -> list[str]:
    if not s:
        return []
    return re.findall(r"\d[\d\s]*(?:[.,]\d+)?\s*€", s, flags=re.I)


def is_plausible_reward(s: str | None) -> bool:
    t = normalize_reward(s)
    if not t:
        return False
    # reject absurd lengths
    if len(t) > 120:
        return False
    # reject zero amounts (0 € / 0,00 €)
    if re.search(r"(?i)^(?:jusqu['’]à\s+)?0(?:[.,]0+)?\s*€", t):
        return False
    # if has euro amount, check range-ish
    nums = re.findall(r"(\d+)", t.replace(" ", "").replace(",", "."))
    for n in nums[:3]:
        try:
            v = int(float(n)) if "." in n else int(n)
            if v == 0:
                return False
            if v > 100_000:
                return False
        except ValueError:
            continue
    return True
