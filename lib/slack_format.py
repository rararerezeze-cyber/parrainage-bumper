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
import copy
from typing import Any

_MAX_TEXT_CHARS = 160
_MAX_SECTION_CHARS = 2900  # Slack mrkdwn section text limit is 3000
_MAX_PLATFORM_ROWS = 10


def guard_workflow_result(result: dict[str, Any], job_status: str) -> dict[str, Any]:
    """A runner-local success is not durable proof after a failed commit step."""
    if job_status == "success":
        return result
    guarded = copy.deepcopy(result)
    guarded.update(ok=False, persist_confirmed=False, platforms=[],
                   human_summary="Exécution incomplète : vérifier le workflow avant toute nouvelle écriture.")
    guarded["errors"] = [{"code": "workflow_incomplete", "detail":
        "Résultat non confirmé durablement. Ne pas relancer une écriture sans vérifier les preuves."}]
    return guarded


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
    concise = _concise_summary(result)
    if concise:
        # Already program-labelled and French ("*Kraken* — ..."); strip
        # markdown emphasis rather than prefixing a redundant label on top
        # of it (that previously produced "kraken set — Kraken* — ...").
        first_line = concise.splitlines()[0].replace("*", "").strip(" :-")
        return _truncate(first_line, _MAX_TEXT_CHARS)
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


# Actions whose result is built from structured fields (plan/result/routing)
# rather than a curated pre-written French reply. Their raw human_summary
# is verbose/technical by design (full cross-platform impact prose, and for
# "status" a literal JSON dump -- see lib.hermes_interface's
# _run_autofresh_command_locked) -- meant for logs/artifacts, not Slack's
# primary view. help/divergences/plateformes are NOT in this set: per
# AGENTS.md, their human_summary IS already the complete, ready-to-send
# French reply and must be relayed verbatim, never rebuilt here.
_STRUCTURED_ACTIONS = {"status", "set", "remove"}


def _concise_status_summary(result: dict[str, Any]) -> str | None:
    parsed = result.get("parsed") or {}
    program = str(parsed.get("program") or "").strip()
    plan = result.get("plan") or {}
    summary = plan.get("summary") or {}
    mapped = summary.get("platforms_mapped")
    pending = summary.get("pending_update")
    in_sync = summary.get("in_sync")
    if mapped is None:
        return None

    label = program.capitalize() if program else "Autofresh"
    line = f"*{label}* — {pending or 0} plateforme(s) à mettre à jour sur {mapped}"
    if in_sync:
        line += f", {in_sync} déjà synchronisée(s)"
    line += "."

    routing = result.get("routing") or {}
    auto_targets = routing.get("automatic_safe_diff_targets") or []
    human_targets = routing.get("human_routed_targets") or []
    lines = [line]
    if auto_targets:
        lines.append(f"✅ Écriture automatique possible : {', '.join(auto_targets)}.")
    if human_targets:
        names = ", ".join(str(h.get("platform")) for h in human_targets)
        lines.append(f"🖐️ Action manuelle requise (hors Slack) : {names}.")
    return "\n".join(lines)


def _concise_set_remove_summary(result: dict[str, Any]) -> str | None:
    parsed = result.get("parsed") or {}
    program = str(parsed.get("program") or "").strip()
    field = parsed.get("field")
    platform = parsed.get("platform")
    action = parsed.get("action")
    if not field:
        return None
    label = program.capitalize() if program else "Autofresh"
    scope = f"plateforme {platform}" if platform else "globale"
    if action == "remove":
        return f"*{label}* — override supprimé : `{field}` (portée : {scope})."
    data = result.get("result") or {}
    old = data.get("old_effective")
    new = data.get("new_effective")
    if data.get("note") == "value_already_effective":
        return f"*{label}* — `{field}` déjà à jour : {new!r} (portée : {scope})."
    return f"*{label}* — `{field}` : {old!r} → {new!r} (portée : {scope})."


def _concise_summary(result: dict[str, Any]) -> str | None:
    """Build the short, curated, French Slack-primary summary for the
    structured (status/set/remove) actions. Returns None for anything else
    so the caller falls back to relaying human_summary verbatim."""
    action = (result.get("parsed") or {}).get("action")
    if action not in _STRUCTURED_ACTIONS:
        return None
    if action == "status":
        return _concise_status_summary(result)
    return _concise_set_remove_summary(result)


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

    concise = _concise_summary(result) if ok else None
    if concise:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": concise[:_MAX_SECTION_CHARS]}})
    else:
        # Meta reads (help/divergences/plateformes) and failures: relay
        # human_summary verbatim -- for the meta reads it is already the
        # complete, ready-to-send French text (AGENTS.md); for failures
        # there is no structured plan/result to summarize from.
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
    # The backend only dispatches writers for a set command. A status/remove
    # confirmation would promise an operation that its safety gate never runs.
    if ok and action == "set" and allow_confirm_button and eligible and not run_writers_requested:
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
