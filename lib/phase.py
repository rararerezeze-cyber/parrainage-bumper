"""Phase Autofresh: BASE (pas de live) vs VALIDATION_LIVE / PRODUCTION.

Pendant BASE:
  - capture / mapping / dry-run / writers prepares OK
  - aucun write reel plateforme
  - aucun canary live auto
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PHASE_PATH = ROOT / "data" / "autofresh-phase.json"


def load_phase() -> dict[str, Any]:
    if PHASE_PATH.exists():
        try:
            return json.loads(PHASE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"phase": "BASE", "live_writes": False, "live_canary": False}


def phase_name() -> str:
    env = (os.environ.get("AUTOFRESH_PHASE") or "").strip().upper()
    if env:
        return env
    return str(load_phase().get("phase") or "BASE").strip().upper()


def live_writes_enabled() -> bool:
    """True seulement si phase live ET flags explicites."""
    if os.environ.get("AUTOFRESH_FORCE_LIVE") == "1":
        return True
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "0":
        return False
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "1":
        return True
    data = load_phase()
    if phase_name() in {"BASE", "BASE_PHASE"}:
        return False
    return bool(data.get("live_writes")) and bool(data.get("live_canary"))


def live_canary_allowed() -> bool:
    if os.environ.get("AUTOFRESH_FORCE_LIVE") == "1":
        return True
    if phase_name() in {"BASE", "BASE_PHASE"}:
        return False
    data = load_phase()
    return bool(data.get("live_canary")) and bool(data.get("live_writes"))


def assert_live_writes_allowed(context: str = "") -> None:
    if not live_writes_enabled():
        raise RuntimeError(
            f"LIVE_WRITES_DISABLED (phase={phase_name()}) {context}".strip()
            + " — BASE phase: dry-run only until BASE_READY_ALL"
        )
