"""Post-verify publique Super-Parrain apres write canary.

Re-fetch annonce publique et compare aux valeurs desirees (offers.json).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

from lib.http_fetch import fetch_text
from lib.renderer import MappingRepository
from lib.super_parrain_content import get_desired_content


@dataclass
class PostVerifyResult:
    program: str
    announcement_url: str | None
    ok: bool
    post_match: bool
    exact_body_match: bool = False
    field_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    published_preview: str | None = None
    desired_preview: str | None = None
    error: str | None = None
    immutable_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "announcement_url": self.announcement_url,
            "ok": self.ok,
            "post_match": self.post_match,
            "exact_body_match": self.exact_body_match,
            "field_checks": self.field_checks,
            "published_preview": (self.published_preview or "")[:400],
            "desired_preview": (self.desired_preview or "")[:400],
            "error": self.error,
            "immutable_ok": self.immutable_ok,
        }


def _extract_public_message(html: str) -> str | None:
    import capture_super_parrain as csp

    return csp.extract_message(html)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def check_fields_in_text(
    published: str,
    desired_values: dict[str, str | None],
    old_values: dict[str, str | None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Verifie presence des nouvelles valeurs et absence des anciennes (si fournies)."""
    checks: dict[str, dict[str, Any]] = {}
    pub = published or ""
    for key, new_val in desired_values.items():
        if not new_val:
            continue
        present = new_val in pub or _norm(new_val) in _norm(pub)
        entry: dict[str, Any] = {
            "desired": new_val,
            "present": present,
        }
        old = (old_values or {}).get(key)
        if old and old != new_val:
            entry["old"] = old
            entry["old_still_present"] = old in pub or _norm(old) in _norm(pub)
            # code/link: old should ideally be gone
            if key in ("personal_code", "personal_link") and entry["old_still_present"] and present:
                # both present is suspicious but may happen in UI chrome
                entry["ambiguous"] = True
        checks[key] = entry
    return checks


def fields_match_ok(field_checks: dict[str, dict[str, Any]]) -> bool:
    if not field_checks:
        return False
    for key, c in field_checks.items():
        if not c.get("present"):
            return False
        # For code/link, refuse if only old remains
        if key in ("personal_code", "personal_link"):
            if c.get("old") and c.get("old_still_present") and not c.get("present"):
                return False
    return True


def verify_public_program(
    program: str,
    language: str = "fr",
    *,
    filled_fields: list[str] | None = None,
    fetch: bool = True,
    published_override: str | None = None,
) -> PostVerifyResult:
    """Re-fetch public + compare.

    post_match=True si:
      - exact body match (published == rendered), OU
      - tous les champs mutables desirees presents (code/link/reward)
        et aucun old code/link residuel critique.
    """
    desired = get_desired_content(program, language)
    try:
        mapping = MappingRepository().load("super-parrain", program, language)
    except Exception as exc:  # noqa: BLE001
        return PostVerifyResult(
            program=program,
            announcement_url=None,
            ok=False,
            post_match=False,
            error=f"mapping: {exc}",
        )

    url = mapping.announcement_url
    if not url:
        return PostVerifyResult(
            program=program,
            announcement_url=None,
            ok=False,
            post_match=False,
            error="no_announcement_url",
        )

    if published_override is not None:
        published = published_override
    elif fetch:
        try:
            html = fetch_text(url)
            published = _extract_public_message(html) or ""
            if not published:
                return PostVerifyResult(
                    program=program,
                    announcement_url=url,
                    ok=False,
                    post_match=False,
                    error="public_message_empty",
                )
        except Exception as exc:  # noqa: BLE001
            return PostVerifyResult(
                program=program,
                announcement_url=url,
                ok=False,
                post_match=False,
                error=f"fetch_failed: {exc}",
            )
    else:
        return PostVerifyResult(
            program=program,
            announcement_url=url,
            ok=False,
            post_match=False,
            error="no_published_text",
        )

    rendered = desired.rendered_body or ""
    exact = bool(rendered) and published == rendered

    want = {
        "personal_code": desired.code,
        "personal_link": desired.link,
        "referee_reward": desired.reward,
    }
    # If only discrete fields were filled, focus checks on those
    if filled_fields:
        key_map = {
            "code": "personal_code",
            "link": "personal_link",
            "title": "title",
            "body": "body",
        }
        focus = set()
        for f in filled_fields:
            focus.add(key_map.get(f, f))
        if "body" in focus:
            # body implies all mutables
            pass
        else:
            want = {k: v for k, v in want.items() if k in focus or k.replace("personal_", "") in filled_fields}

    old_vals = dict(mapping.platform_values or {})
    checks = check_fields_in_text(published, want, old_vals)

    # Exact body is strongest signal
    if exact:
        post_match = True
    else:
        # Require code present at minimum when code was desired/changed
        post_match = fields_match_ok(checks)
        # If we filled body, require exact or near-exact (normalize whitespace)
        if filled_fields and "body" in filled_fields and rendered:
            post_match = _norm(published) == _norm(rendered)

    # Immutable: Discord / structure markers should still exist if in golden template
    immutable_ok = True
    if "discord.gg" in (rendered or "") and "discord.gg" not in published:
        immutable_ok = False
        post_match = False

    return PostVerifyResult(
        program=program,
        announcement_url=url,
        ok=post_match and immutable_ok,
        post_match=post_match,
        exact_body_match=exact,
        field_checks=checks,
        published_preview=published[:500],
        desired_preview=(rendered or "")[:500],
        immutable_ok=immutable_ok,
        error=None if post_match else "post_match_false",
    )
