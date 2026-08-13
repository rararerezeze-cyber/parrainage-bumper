"""Fallback parser for unmonitorable sources — never invents live data.

Used only to mark program as manual/unmonitorable while keeping pipeline shape.
"""
from __future__ import annotations

from lib.monitor.models import Confidence, NormalizedOffer, SourceConfig


def parse_static_from_canonical(
    html: str,
    cfg: SourceConfig,
    offer: dict | None = None,
) -> NormalizedOffer:
    return NormalizedOffer(
        program=cfg.program,
        fields={},
        confidence=Confidence.REJECT,
        parser=cfg.parser,
        source_url=cfg.source_url,
        raw_fingerprint="",
        notes=["unmonitorable_or_manual_source", "no_live_extraction"],
    )
