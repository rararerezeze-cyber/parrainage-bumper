"""Gardes write: mapping absent du compte = jamais write/recreate auto."""
from __future__ import annotations

from typing import Any

# status / quality values that forbid any content write or auto-create
WRITE_BLOCKED_STATUSES = frozenset(
    {
        "NOT_PRESENT_ON_ACCOUNT",
        "STALE_MAPPING",
        "NOT_ON_ACCOUNT",
        "NOT_ON_PUBLIC_PROFILE",
        "missing_source",
    }
)

WRITE_BLOCKED_QUALITIES = frozenset(
    {
        "not_on_account",
        "stale_mapping",
        "invalid_pending_source",
        "missing_source",
    }
)


def load_mapping_raw(platform: str, program: str, language: str = "fr") -> dict[str, Any]:
    import json
    from lib.paths import mapping_path

    path = mapping_path(platform, program, language)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_blocked_reason(
    platform: str,
    program: str,
    language: str = "fr",
    raw: dict[str, Any] | None = None,
) -> str | None:
    """None si write autorise; sinon raison bloquante.

    Regle: une annonce/mapping absente du compte authentifie ne doit jamais
    provoquer de write ni de recreation automatique.
    """
    data = raw if raw is not None else load_mapping_raw(platform, program, language)
    if not data:
        return "no_mapping_file"

    status = str(data.get("status") or "").strip()
    quality = str(data.get("quality") or "").strip()
    template_status = str(data.get("template_status") or "").strip()
    sync_mode = str(data.get("sync_mode") or "").strip().upper()

    if status in WRITE_BLOCKED_STATUSES:
        return f"status={status}"
    if quality in WRITE_BLOCKED_QUALITIES:
        return f"quality={quality}"
    if template_status in {"missing_source", "manual_review_required"}:
        return f"template_status={template_status}"
    if data.get("write_eligible") is False:
        return "write_eligible=false"
    if sync_mode in {"MANUAL", "MANUAL_REVIEW_REQUIRED"} and data.get("force_write") is not True:
        # MANUAL alone is not always block — only if also no edit_url and not present
        pass

    # No authenticated edit target and explicit not-on-account notes
    if not data.get("edit_url") and not data.get("announcement_url"):
        notes = (data.get("notes") or "").lower()
        if any(
            x in notes
            for x in (
                "not on account",
                "absent du compte",
                "not_present",
                "stale",
            )
        ):
            return "no_url_and_stale_notes"

    return None


def assert_write_allowed(platform: str, program: str, language: str = "fr") -> None:
    reason = write_blocked_reason(platform, program, language)
    if reason:
        raise RuntimeError(
            f"WRITE_BLOCKED {platform}/{program}.{language}: {reason} "
            f"— pas de write ni recreation auto (mapping stale / absent du compte)"
        )
