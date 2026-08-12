"""Phase Autofresh: BASE → VALIDATION_LIVE → PRODUCTION.

WRITE_PREPARED ≠ WRITE_VERIFIED.
Pendant VALIDATION_LIVE: writers activables plateforme par plateforme
et programme canary par programme canary.
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


def save_phase(data: dict[str, Any]) -> None:
    PHASE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phase_name() -> str:
    env = (os.environ.get("AUTOFRESH_PHASE") or "").strip().upper()
    if env:
        return env
    return str(load_phase().get("phase") or "BASE").strip().upper()


def live_writes_enabled(platform: str | None = None) -> bool:
    """Live content writes allowed?

    - BASE: always false (unless AUTOFRESH_FORCE_LIVE=1)
    - VALIDATION_LIVE: only platforms in enabled_platforms or write_verified
    - AUTOFRESH_LIVE_WRITES=0 force off
    """
    if os.environ.get("AUTOFRESH_FORCE_LIVE") == "1":
        return True
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "0":
        return False
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "1" and platform is None:
        return True

    data = load_phase()
    name = phase_name()
    if name in {"BASE", "BASE_PHASE"}:
        return False
    if not data.get("live_writes"):
        return False

    if platform is None:
        # Phase allows live somewhere
        return bool(data.get("enabled_platforms") or data.get("write_verified") or data.get("live_canary"))

    plat = platform.strip().lower()
    verified = {str(p).lower() for p in (data.get("write_verified") or [])}
    if plat in verified:
        return True
    enabled = {str(p).lower() for p in (data.get("enabled_platforms") or [])}
    if plat in enabled:
        return True
    return False


def live_canary_allowed(platform: str | None = None) -> bool:
    if os.environ.get("AUTOFRESH_FORCE_LIVE") == "1":
        return True
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "0":
        return False
    if phase_name() in {"BASE", "BASE_PHASE"}:
        return False
    data = load_phase()
    if not data.get("live_canary") or not data.get("live_writes"):
        return False
    if platform is None:
        return True
    return live_writes_enabled(platform)


def mark_write_verified(platform: str) -> None:
    data = load_phase()
    verified = list(data.get("write_verified") or [])
    if platform not in verified:
        verified.append(platform)
    data["write_verified"] = verified
    # keep in enabled too
    enabled = list(data.get("enabled_platforms") or [])
    if platform not in enabled:
        enabled.append(platform)
    data["enabled_platforms"] = enabled
    save_phase(data)


def assert_live_writes_allowed(context: str = "", platform: str | None = None) -> None:
    if not live_writes_enabled(platform):
        raise RuntimeError(
            f"LIVE_WRITES_DISABLED (phase={phase_name()} platform={platform}) {context}".strip()
            + " — only WRITE_VERIFIED / enabled canary platforms may write"
        )
