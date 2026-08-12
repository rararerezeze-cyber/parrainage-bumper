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
    """Live content writes allowed for Telegram operator path?

    Strict rule (END-TO-END phase):
    - Only platforms marked WRITE_VERIFIED in data/platform-write-status.json
      (and mirrored in phase write_verified) may receive live Telegram updates.
    - CANARY_READY platforms are NOT live for Telegram; use explicit canary tools.
    - BASE: always false (unless AUTOFRESH_FORCE_LIVE=1)
    - AUTOFRESH_LIVE_WRITES=0 force off
    """
    if os.environ.get("AUTOFRESH_FORCE_LIVE") == "1":
        return True
    if os.environ.get("AUTOFRESH_LIVE_WRITES") == "0":
        return False

    data = load_phase()
    name = phase_name()
    if name in {"BASE", "BASE_PHASE"}:
        return False
    if not data.get("live_writes"):
        return False

    # Prefer strict registry
    try:
        from lib.write_status import is_write_verified, summary as write_summary

        if platform is None:
            return write_summary().get("write_verified_count", 0) > 0 or bool(
                data.get("write_verified")
            )
        if is_write_verified(platform):
            return True
        # phase list as mirror (must still be WRITE_VERIFIED in registry ideally)
        verified = {str(p).lower() for p in (data.get("write_verified") or [])}
        return platform.strip().lower() in verified
    except Exception:
        pass

    if platform is None:
        return bool(data.get("write_verified"))

    plat = platform.strip().lower()
    verified = {str(p).lower() for p in (data.get("write_verified") or [])}
    return plat in verified


def live_canary_allowed(platform: str | None = None) -> bool:
    """Explicit canary path (not Telegram bulk). CANARY_READY platforms only."""
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
    try:
        from lib.write_status import get_platform_status, STATUS_CANARY_READY, STATUS_WRITE_VERIFIED

        st = get_platform_status(platform)
        return st in {STATUS_CANARY_READY, STATUS_WRITE_VERIFIED}
    except Exception:
        enabled = {str(p).lower() for p in (data.get("enabled_platforms") or [])}
        return platform.strip().lower() in enabled


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
