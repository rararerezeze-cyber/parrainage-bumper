"""Slack Block Kit rendering for Autofresh operator results.

Pure functions — no Slack client, no network call. Takes the same
result dict shape lib.hermes_interface.run_autofresh_command() /
handle_request() return (see docs/hermes-autofresh-interface.md) and
produces a Slack chat.postMessage payload.

Design (mirrors the text/blocks separation used elsewhere in this
project's Slack work):
  - ``text``  : short, deterministic, single-line fallback (notification
    preview / accessibility). Never the full report.
  - ``blocks``: the full rich content shown when the channel is open.

Generic and channel-agnostic: nothing here special-cases #hermes,
#betstats or #bonusparrain — the caller passes whichever channel id the
command came from.
"""
from __future__ import annotations

import json
from typing import Any

_MAX_TEXT_CHARS = 160
_MAX_SECTION_CHARS = 2900  # Slack mrkdwn section text limit is 3000
_MAX_PLATFORM_ROWS = 10


def _truncate(s: str, max_chars: int) -> str:
    s = " ".join((s or "").split())
    if len(s) <= max_chars:
        return s
    cut = s[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def notification_text(result: dict[str, Any]) -> str:
    """Short single-line fallback for the ``text`` field."""
    if result.get("ok") is False:
        errs = result.get("errors") or []
        detail = (errs[0].get("detail") if errs else None) or "erreur inconnue"
        return _truncate(f"Autofresh — échec : {detail}", _MAX_TEXT_CHARS)
    parsed = result.get("parsed") or {}
    program = (parsed.get("program") or "").strip()
    action = (parsed.get("action") or "status").strip()
    human = (result.get("human_summary") or "").strip()
    first_line = human.splitlines()[0] if human else ""
    first_line = first_line.lstrip("#").strip(" :-")
    label = f"{program} {action}".strip() if program else action
    if first_line:
        return _truncate(f"{label} — {first_line}" if label else first_line, _MAX_TEXT_CHARS)
    return _truncate(label or "Autofresh — terminé.", _MAX_TEXT_CHARS)


def _platform_status_line(row: dict[str, Any]) -> str:
    plat = row.get("platform") or "?"
    status = row.get("status") or row.get("write_mode") or "?"
    marker = "✅" if row.get("can_auto_write") else ("✏️" if row.get("route") == "HUMAN_SAVE_REQUIRED" else "•")
    changed = row.get("changed_fields") or {}
    extra = f" ({', '.join(list(changed.keys())[:3])})" if changed else ""
    return f"{marker} `{plat}` — {status}{extra}"


def _platforms_block(platforms: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not platforms:
        return None
    rows = platforms[:_MAX_PLATFORM_ROWS]
    lines = [_platform_status_line(r) for r in rows]
    if len(platforms) > _MAX_PLATFORM_ROWS:
        lines.append(f"… +{len(platforms) - _MAX_PLATFORM_ROWS} autre(s)")
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)[:_MAX_SECTION_CHARS]},
    }


def _errors_block(errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not errors:
        return None
    lines = [f"• `{e.get('code')}` : {e.get('detail')}" for e in errors[:10]]
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": ":x: *Erreurs*\n" + "\n".join(lines)[:_MAX_SECTION_CHARS]},
    }


def _writer_eligible_rows(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        p
        for p in (platforms or [])
        if p.get("can_auto_write") and p.get("status") == "pending_update"
    ]


def _confirm_button_block(
    *,
    command: str,
    requester: str,
    correlation_id: str | None,
    program: str | None,
) -> dict[str, Any]:
    value = json.dumps(
        {
            "command": command,
            "requester": requester,
            "correlation_id": correlation_id,
            "program": program,
        },
        ensure_ascii=False,
    )
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "style": "primary",
                "action_id": "autofresh_confirm_write",
                "text": {"type": "plain_text", "text": "Confirmer l'écriture"},
                "value": value[:2000],
                "confirm": {
                    "title": {"type": "plain_text", "text": "Confirmer l'écriture ?"},
                    "text": {
                        "type": "mrkdwn",
                        "text": "Ceci lance un writer vérifié réel sur la/les plateforme(s) éligible(s).",
                    },
                    "confirm": {"type": "plain_text", "text": "Écrire"},
                    "deny": {"type": "plain_text", "text": "Annuler"},
                },
            }
        ],
    }


def render_result(
    result: dict[str, Any],
    *,
    run_writers_requested: bool = False,
    allow_confirm_button: bool = True,
) -> dict[str, Any]:
    """Build a ``chat.postMessage`` payload body (without ``channel``) from
    an Autofresh operator result dict.

    ``run_writers_requested``: whether THIS dispatch already had
    run_writers=true (in which case a confirm button would be redundant —
    the write either already happened or was attempted).
    """
    ok = bool(result.get("ok"))
    parsed = result.get("parsed") or {}
    program = parsed.get("program")
    action = parsed.get("action") or "status"
    command = result.get("command") or ""
    correlation_id = result.get("correlation_id")
    requester = ((result.get("auth") or {}).get("identity")) or "slack"

    header_icon = "✅" if ok else "❌"
    header_title = f"{header_icon} Autofresh — {program or action}".strip()

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_title[:150]}},
    ]

    human = (result.get("human_summary") or "").strip()
    if human:
        for i in range(0, len(human), _MAX_SECTION_CHARS):
            chunk = human[i : i + _MAX_SECTION_CHARS]
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    err_block = _errors_block(result.get("errors") or [])
    if err_block:
        blocks.append(err_block)

    platforms = result.get("platforms") or []
    plat_block = _platforms_block(platforms)
    if plat_block:
        blocks.append(plat_block)

    eligible = _writer_eligible_rows(platforms)
    if ok and allow_confirm_button and eligible and not run_writers_requested:
        blocks.append(_confirm_button_block(
            command=command,
            requester=str(requester),
            correlation_id=correlation_id,
            program=program,
        ))

    ctx_parts = []
    if correlation_id:
        ctx_parts.append(f"correlation_id: `{correlation_id}`")
    if requester:
        ctx_parts.append(f"requester: `{requester}`")
    if ctx_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(ctx_parts)}],
            }
        )

    return {
        "text": notification_text(result),
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }
