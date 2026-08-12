"""Extract structured public data (JSON-LD, embedded JSON) — no login, no bypass."""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Any


def extract_json_ld(html: str) -> list[Any]:
    out: list[Any] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    ):
        raw = unescape(m.group(1)).strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


def extract_next_data(html: str) -> Any | None:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def extract_embedded_json_blobs(html: str, limit: int = 20) -> list[Any]:
    """Best-effort small JSON objects in script tags (not full page dumps)."""
    out: list[Any] = []
    for m in re.finditer(
        r"<script[^>]*>(.*?)</script>",
        html or "",
        flags=re.I | re.S,
    ):
        body = m.group(1)
        low = body.lower()
        if not any(k in low for k in ("referral", "reward", "bonus", "parrain", "cashback")):
            continue
        for jm in re.finditer(r"(\{[^{}]{20,2000}\})", body):
            try:
                obj = json.loads(jm.group(1))
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
            if len(out) >= limit:
                return out
    return out


def _has_money(s: str) -> bool:
    return bool(re.search(r"\d+\s*€|\$\s*\d+|\d+\s*eur", s, re.I))


def _has_pct(s: str) -> bool:
    return bool(re.search(r"\d+\s*%", s))


def structured_reward_hints(html: str) -> dict[str, str]:
    """Pull likely reward fields from structured blobs if unambiguous."""
    fields: dict[str, str] = {}
    blobs: list[Any] = []
    blobs.extend(extract_json_ld(html))
    nd = extract_next_data(html)
    if nd is not None:
        blobs.append(nd)
    blobs.extend(extract_embedded_json_blobs(html))

    rewards: list[str] = []
    mins: list[str] = []
    days: list[str] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)
                elif isinstance(v, (str, int, float)):
                    vs = str(v)
                    if any(x in kl for x in ("reward", "bonus", "amount", "cash", "credit")):
                        if _has_money(vs) or _has_pct(vs):
                            rewards.append(vs.strip())
                    if any(x in kl for x in ("min_deposit", "minimum", "deposit_min", "min_spend", "threshold")):
                        if _has_money(vs) or re.search(r"\d+", vs):
                            mins.append(vs.strip())
                    if any(x in kl for x in ("day", "deadline", "expiry", "valid_for", "qualification")):
                        if re.search(r"\d+", vs):
                            days.append(vs.strip())
        elif isinstance(obj, list):
            for it in obj[:50]:
                walk(it, depth + 1)

    for b in blobs:
        walk(b)

    uniq: list[str] = []
    for r in rewards:
        if r not in uniq:
            uniq.append(r)
    if len(uniq) == 1:
        fields["referee_reward"] = uniq[0]
        fields["_structured"] = "1"
    elif len(uniq) > 1:
        fields["_structured_multi"] = str(uniq[:5])
    if len(mins) == 1:
        fields["min_deposit"] = mins[0]
    if len(days) == 1:
        fields["qualification_days"] = days[0]
    if blobs:
        fields["_has_structured"] = "1"
    if nd is not None:
        fields["_has_next_data"] = "1"
    return fields
