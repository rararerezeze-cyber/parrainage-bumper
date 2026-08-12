"""Try structured public data first, then generic HTML fallback."""
from __future__ import annotations

import hashlib

from lib.monitor.models import Confidence, FailureCode, NormalizedOffer, SourceConfig
from lib.monitor.normalize import is_plausible_reward, normalize_field, normalize_reward
from lib.monitor.parsers.generic_reward_html import parse_generic_reward_html
from lib.monitor.structured import structured_reward_hints


def parse_structured_first(
    html: str,
    cfg: SourceConfig,
    offer: dict | None = None,
) -> NormalizedOffer:
    raw_fp = hashlib.sha256((html or "")[:50000].encode("utf-8", errors="replace")).hexdigest()[:16]
    hints = structured_reward_hints(html or "")
    notes: list[str] = []

    if hints.get("_has_structured"):
        notes.append("structured_data_present")
    if hints.get("_has_next_data"):
        notes.append("next_data_present")
    if hints.get("_structured_multi"):
        notes.append(f"structured_multi={hints['_structured_multi']}")
    elif hints.get("referee_reward"):
        reward = normalize_reward(hints["referee_reward"])
        if reward and is_plausible_reward(reward):
            fields: dict[str, str | None] = {"referee_reward": reward}
            for k in ("min_deposit", "qualification_days"):
                if hints.get(k):
                    fields[k] = normalize_field(k, hints[k])
            conf = Confidence.HIGH
            notes.append("structured_single_reward")
            generic = parse_generic_reward_html(html, cfg, offer)
            if generic.fields.get("conditions"):
                fields["conditions"] = normalize_field(
                    "conditions", generic.fields["conditions"]
                )
            return NormalizedOffer(
                program=cfg.program,
                fields={k: v for k, v in fields.items() if v},
                confidence=conf,
                parser="structured_first",
                source_url=cfg.source_url,
                raw_fingerprint=raw_fp,
                notes=notes,
                failure_code=FailureCode.NONE,
                extraction_mode="structured_json",
            )

    # Fallback generic HTML (discovery / REVIEW only — never final proof)
    out = parse_generic_reward_html(html, cfg, offer)
    out.parser = "structured_first+generic_reward_html"
    out.notes = notes + list(out.notes)
    if out.confidence == Confidence.HIGH:
        out.confidence = Confidence.REVIEW
        out.notes.append("generic_only_not_final")
    return out
