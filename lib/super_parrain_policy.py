"""Politique Autofresh Super-Parrain: canary vs rollout vs off.

Par defaut (securite premier live):
  AUTOFRESH_SUPER=1
  AUTOFRESH_MODE=canary
  AUTOFRESH_CANARY_PROGRAMS=kraken

Seuls les programmes canary recoivent un prefill contenu.
Les autres: BUMP_ONLY (Enregistrer historique, contenu inchange).

Apres canary post_match=true:
  AUTOFRESH_MODE=full  (ou CANARY_PROGRAMS=*)
"""
from __future__ import annotations

import os
from typing import Mapping


DEFAULT_CANARY_PROGRAMS = ("kraken",)


def autofresh_enabled(env: Mapping[str, str] | None = None) -> bool:
    e = env if env is not None else os.environ
    if e.get("AUTOFRESH_SUPER", "1") == "0":
        return False
    mode = (e.get("AUTOFRESH_MODE") or "canary").strip().lower()
    return mode != "off"


def parse_canary_programs(env: Mapping[str, str] | None = None) -> frozenset[str] | None:
    """None = tous les programmes (full). frozenset = liste canary uniquement."""
    e = env if env is not None else os.environ
    mode = (e.get("AUTOFRESH_MODE") or "canary").strip().lower()
    raw = (e.get("AUTOFRESH_CANARY_PROGRAMS") or "").strip()

    if mode in ("full", "rollout", "all"):
        return None
    if raw in ("*", "all"):
        return None
    if mode == "off":
        return frozenset()

    # canary (default)
    if not raw:
        return frozenset(DEFAULT_CANARY_PROGRAMS)
    programs = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return frozenset(programs)


def should_prefill_content(
    program: str | None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Decide si ce programme peut recevoir un prefill Autofresh.

    Returns (allowed, reason).
    """
    e = env if env is not None else os.environ
    if not autofresh_enabled(e):
        return False, "autofresh_off"
    if not program:
        return False, "program_unknown"
    if e.get("AUTOFRESH_STOP") == "1":
        return False, "autofresh_stopped_after_canary_fail"

    allowed = parse_canary_programs(e)
    if allowed is None:
        return True, "full_rollout"
    prog = program.strip().lower()
    if prog in allowed:
        return True, "canary"
    return False, "bump_only_not_canary"


def policy_snapshot(env: Mapping[str, str] | None = None) -> dict:
    e = env if env is not None else os.environ
    canary = parse_canary_programs(e)
    return {
        "autofresh_enabled": autofresh_enabled(e),
        "mode": (e.get("AUTOFRESH_MODE") or "canary").strip().lower(),
        "canary_programs": sorted(canary) if canary is not None else ["*"],
        "stop_flag": e.get("AUTOFRESH_STOP") == "1",
    }
