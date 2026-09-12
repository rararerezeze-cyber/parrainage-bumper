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

_FIELD_LABELS = {
    "personal_code": "code de parrainage",
    "personal_link": "lien de parrainage",
    "referee_reward": "gain filleul",
    "referrer_reward": "gain parrain",
    "conditions": "conditions",
    "min_deposit": "dépôt minimum",
    "min_spend": "dépense minimum",
    "min_trade": "montant de trade minimum",
    "trade_min": "montant de trade minimum",
    "transaction_count": "nombre de transactions",
    "qualification_days": "délai de qualification",
    "expiry_date": "date d'expiration",
    "reward_type": "type de récompense",
    "title": "titre",
}

_PLATFORM_LABELS = {
    "super-parrain": "Super-Parrain",
    "parrainage-co": "Parrainage.co",
    "code-parrainage": "Code-Parrainage",
    "1parrainage": "1Parrainage",
    "referralcodes": "ReferralCodes",
    "referralcode-tv": "ReferralCode.tv",
    "referraldrop": "ReferralDrop",
}

_STATUS_LABELS = {
    "pending_update": "différence à traiter",
    "in_sync": "à jour",
    "blocked": "bloquée",
    "error": "en erreur",
}

_ERROR_LABELS = {
    "workflow_incomplete": "Exécution interrompue",
    "parse_error": "Commande non reconnue",
    "unauthorized": "Accès refusé",
    "apply_error": "Modification refusée",
    "persist_unconfirmed": "Enregistrement non confirmé",
    "persist_verify_error": "Vérification de l'enregistrement impossible",
    "serialized_lock_timeout": "Une autre commande est déjà en cours",
    "unknown_program": "Programme inconnu",
    "empty_command": "Commande vide",
    "invalid_command": "Commande invalide",
}


def guard_workflow_result(result: dict[str, Any], job_status: str) -> dict[str, Any]:
    """Never present a failed workflow as a durable success."""
    if job_status == "success":
        return result
    guarded = copy.deepcopy(result)
    action = str((guarded.get("parsed") or {}).get("action") or "")
    read_only = action in {"status", "list", "help", "divergences", "plateformes_program"}
    guarded.update(ok=False, persist_confirmed=False, platforms=[])
    if read_only:
        guarded["human_summary"] = (
            "La commande de consultation a été interrompue. "
            "Aucune écriture n'a été lancée ; tu peux simplement réessayer."
        )
        detail = "La consultation n'a pas été finalisée. Aucune écriture n'a été effectuée."
    else:
        guarded["human_summary"] = (
            "L'exécution a été interrompue avant confirmation durable du résultat. "
            "Vérifie le workflow avant de relancer une écriture."
        )
        detail = "Le résultat n'a pas pu être confirmé durablement."
    guarded["errors"] = [{"code": "workflow_incomplete", "detail": detail}]
    return guarded


def _truncate(s: str, max_chars: int) -> str:
    s = " ".join((s or "").split())
    if len(s) <= max_chars:
        return s
    cut = s[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"



def _field_label(field: Any) -> str:
    key = str(field or "")
    return _FIELD_LABELS.get(key, key.replace("_", " ") or "valeur")


def _platform_label(platform: Any) -> str:
    key = str(platform or "")
    return _PLATFORM_LABELS.get(key, key or "plateforme")


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "non défini"
    text = str(value)
    if text.startswith("http://") or text.startswith("https://"):
        return f"<{text}>"
    return text


def _result_title(result: dict[str, Any]) -> str:
    parsed = result.get("parsed") or {}
    action = str(parsed.get("action") or "")
    program = str(parsed.get("program") or "").strip()
    program_label = program.capitalize() if program else ""
    if action == "help":
        topic = str((result.get("result") or {}).get("topic") or parsed.get("help_topic") or "menu")
        return {
            "menu": "Aide",
            "exemples": "Exemples",
            "plateformes": "Plateformes",
            "bump": "Bumpers",
        }.get(topic, "Aide")
    if action == "divergences":
        return f"Divergences — {program_label}" if program_label else "Divergences"
    if action == "plateformes_program":
        return f"Plateformes — {program_label}" if program_label else "Plateformes"
    if action == "list":
        return f"Valeurs personnalisées — {program_label}" if program_label else "Valeurs personnalisées"
    if action == "status":
        return f"Statut — {program_label}" if program_label else "Statut"
    if action == "set":
        return f"Modification — {program_label}" if program_label else "Modification"
    if action == "remove":
        return f"Suppression — {program_label}" if program_label else "Suppression"
    return program_label or "Résultat"


def _clean_error_detail(code: str, detail: Any) -> str:
    text = str(detail or "").strip()
    if code == "parse_error":
        if text.startswith("unknown_program:"):
            return (
                f"Enseigne inconnue : {text.split(':', 1)[1]}. "
                "Utilise /autofresh enseignes pour voir les noms disponibles."
            )
        if text.startswith("unknown_field:"):
            return f"Valeur non reconnue : {text.split(':', 1)[1]}. Utilise /autofresh aide."
        if text in {"empty_message", "invalid_message"}:
            return "Commande vide ou invalide. Utilise /autofresh aide."
    if code == "unauthorized":
        return "Cette commande n'est pas autorisée pour ce compte Slack."
    if code == "serialized_lock_timeout":
        return "Une autre modification est déjà en cours. Réessaie dans quelques instants."
    return text


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or singular + "s")


def notification_text(result: dict[str, Any]) -> str:
    """Short single-line fallback for Slack notifications/accessibility."""
    if result.get("ok") is False:
        errs = result.get("errors") or []
        first = errs[0] if errs else {}
        code = str(first.get("code") or "")
        detail = _clean_error_detail(code, first.get("detail")) or "erreur inconnue"
        return _truncate(f"AutoFresh — échec : {detail}", _MAX_TEXT_CHARS)
    concise = _concise_summary(result)
    if concise:
        first_line = concise.splitlines()[0].replace("*", "").strip(" :-")
        return _truncate(first_line, _MAX_TEXT_CHARS)
    human = (result.get("human_summary") or "").strip()
    first_line = human.splitlines()[0] if human else ""
    if first_line:
        return _truncate(first_line.replace("*", "").strip(" :-"), _MAX_TEXT_CHARS)
    return _truncate(f"AutoFresh — {_result_title(result)}", _MAX_TEXT_CHARS)


def _platform_status_line(row: dict[str, Any]) -> str:
    platform = _platform_label(row.get("platform"))
    status = str(row.get("status") or row.get("write_mode") or "")
    route = str(row.get("route") or "")
    changed = row.get("changed_fields") or {}
    fields = ", ".join(_field_label(k) for k in list(changed.keys())[:4])

    if status == "in_sync":
        return f"✅ `{platform}` — à jour"

    if status == "pending_update":
        if row.get("can_auto_write"):
            detail = "à synchroniser — écriture disponible après confirmation"
            marker = "🟠"
        elif route == "HUMAN_SAVE_REQUIRED":
            detail = "à synchroniser — sauvegarde manuelle requise"
            marker = "🖐️"
        elif route in {"NEVER_AUTO_COMMIT", "AUTH_BLOCKED_MANUAL"}:
            detail = "différence détectée — mise à jour manuelle uniquement"
            marker = "⚠️"
        else:
            detail = "différence détectée"
            marker = "⚠️"
        if fields:
            detail += f" ({fields})"
        return f"{marker} `{platform}` — {detail}"

    label = _STATUS_LABELS.get(status, status.replace("_", " ") or "état inconnu")
    if fields:
        label += f" ({fields})"
    return f"• `{platform}` — {label}"


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
    lines = []
    for err in errors[:10]:
        code = str(err.get("code") or "")
        label = _ERROR_LABELS.get(code, "Erreur")
        detail = _clean_error_detail(code, err.get("detail"))
        lines.append(f"• *{label}*" + (f" — {detail}" if detail else ""))
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "❌ *Problème rencontré*\n" + "\n".join(lines)[:_MAX_SECTION_CHARS]},
    }


# Actions whose result is built from structured fields (plan/result/routing)
# rather than a curated pre-written French reply. Their raw human_summary
# is verbose/technical by design (full cross-platform impact prose, and for
# "status" a literal JSON dump -- see lib.hermes_interface's
# _run_autofresh_command_locked) -- meant for logs/artifacts, not Slack's
# primary view. help/divergences/plateformes are NOT in this set: per
# AGENTS.md, their human_summary IS already the complete, ready-to-send
# French reply and must be relayed verbatim, never rebuilt here.
_STRUCTURED_ACTIONS = {"status", "list", "divergences", "set", "remove"}


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

    label = program.capitalize() if program else "AutoFresh"
    mapped_i = int(mapped or 0)
    pending_i = int(pending or 0)
    in_sync_i = int(in_sync or 0)
    lines = [
        f"*{label}* — {mapped_i} {_count_label(mapped_i, 'plateforme suivie', 'plateformes suivies')} : "
        f"{in_sync_i} à jour, {pending_i} {_count_label(pending_i, 'avec une différence', 'avec une différence')}."
    ]

    routing = result.get("routing") or {}
    auto_targets = routing.get("automatic_safe_diff_targets") or []
    deferred_targets = routing.get("deferred_cycle_targets") or []
    human_targets = routing.get("human_routed_targets") or []
    blocked_targets = routing.get("blocked_targets") or []
    if auto_targets:
        names = ", ".join(_platform_label(p) for p in auto_targets)
        lines.append(f"🟠 Compatible avec une mise à jour après confirmation : {names}.")
    if deferred_targets:
        names = ", ".join(_platform_label(p) for p in deferred_targets)
        lines.append(f"🔵 Mise à jour au prochain cycle automatique si nécessaire : {names}.")
    if human_targets:
        names = ", ".join(_platform_label(h.get("platform")) for h in human_targets)
        lines.append(f"🖐️ Intervention manuelle nécessaire : {names}.")
    if blocked_targets:
        names = ", ".join(_platform_label(p) for p in blocked_targets)
        lines.append(f"⚪ Mise à jour manuelle uniquement : {names}.")
    return "\n".join(lines)


def _concise_list_summary(result: dict[str, Any]) -> str:
    parsed = result.get("parsed") or {}
    program = str(parsed.get("program") or "").strip()
    label = program.capitalize() if program else "AutoFresh"
    overrides = (result.get("result") or {}).get("overrides") or []
    if not overrides:
        return f"*{label}* — aucune valeur personnalisée enregistrée."

    lines = [f"*{label}* — valeurs personnalisées :"]
    for item in overrides[:20]:
        field = _field_label(item.get("field"))
        value = _format_value(item.get("value"))
        platform = item.get("platform")
        scope = _platform_label(platform) if platform else "toutes les plateformes compatibles"
        lines.append(f"• *{field}* — {value} · {scope}")
    if len(overrides) > 20:
        lines.append(f"… +{len(overrides) - 20} autre(s)")
    return "\n".join(lines)


def _concise_divergences_summary(result: dict[str, Any]) -> str:
    parsed = result.get("parsed") or {}
    program = str(parsed.get("program") or "").strip()
    label = program.capitalize() if program else "AutoFresh"
    items = (result.get("result") or {}).get("divergences") or []
    if not items:
        return f"*{label}* — aucune divergence en attente."

    lines = [f"*{label}* — {len(items)} {_count_label(len(items), 'divergence en attente', 'divergences en attente')} :"]
    for item in items[:20]:
        platform = _platform_label(item.get("platform"))
        field = _field_label(item.get("field"))
        current = _format_value(item.get("curated_value"))
        observed = _format_value(item.get("observed_value"))
        lines.append(f"• `{platform}` — *{field}* : {current} → {observed}")
    if len(items) > 20:
        lines.append(f"… +{len(items) - 20} autre(s)")
    return "\n".join(lines)


def _concise_set_remove_summary(result: dict[str, Any]) -> str | None:
    parsed = result.get("parsed") or {}
    program = str(parsed.get("program") or "").strip()
    field = parsed.get("field")
    platform = parsed.get("platform")
    action = parsed.get("action")
    if not field:
        return None

    label = program.capitalize() if program else "AutoFresh"
    field_label = _field_label(field)
    scope = f"sur {_platform_label(platform)}" if platform else "pour toutes les plateformes compatibles"

    if action == "remove":
        return f"*{label}* — valeur personnalisée supprimée : *{field_label}* ({scope})."

    data = result.get("result") or {}
    old = data.get("old_effective")
    new = data.get("new_effective")
    if data.get("note") == "value_already_effective":
        return f"*{label}* — *{field_label}* déjà à jour : {_format_value(new)} ({scope})."
    return (
        f"*{label}* — *{field_label}* modifié ({scope}) : "
        f"{_format_value(old)} → {_format_value(new)}."
    )


def _concise_summary(result: dict[str, Any]) -> str | None:
    """Build the short, curated, French Slack-primary summary for the
    structured (status/set/remove) actions. Returns None for anything else
    so the caller falls back to relaying human_summary verbatim."""
    action = (result.get("parsed") or {}).get("action")
    if action not in _STRUCTURED_ACTIONS:
        return None
    if action == "status":
        return _concise_status_summary(result)
    if action == "list":
        return _concise_list_summary(result)
    if action == "divergences":
        return _concise_divergences_summary(result)
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
                        "text": "Cette confirmation lance la mise à jour réelle des plateformes compatibles qui présentent une différence sûre.",
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
    header_title = f"{header_icon} AutoFresh — {_result_title(result)}".strip()

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

    # Keep successful messages clean. A short technical reference is useful
    # only when the workflow failed and the operator may need to inspect logs.
    if not ok and correlation_id:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Référence technique : `{str(correlation_id)[:12]}`",
                    }
                ],
            }
        )

    return {
        "text": notification_text(result),
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }
