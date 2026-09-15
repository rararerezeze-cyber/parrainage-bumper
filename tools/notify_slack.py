"""Deliver the sanitized runtime outbox to Slack without a local relay.

Scheduled notifications are rendered as an operator UI: important events carry
safe Block Kit actions so the normal workflow is click-first, not command-first.

Safety invariants:
- notification buttons never bypass the existing GitHub operator workflow;
- accepting a monitor candidate dispatches the same unarmed "set" preview used
  by /autofresh (run_writers=false);
- actual compatible platform writes still require the separate
  "Confirmer l'écriture" step rendered by lib.slack_format;
- contradictory/failed verification events expose read-only inspection actions;
- "Plus tard" is a UI acknowledgement only and never mutates AutoFresh state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.notify import FIELDS, read_events, build_event, should_notify
from lib.offers import OffersRepository


_PLATFORM_LABELS = {
    "super-parrain": "Super-Parrain",
    "parrainage-co": "Parrainage.co",
    "code-parrainage": "Code-Parrainage",
    "1parrainage": "1Parrainage",
    "referralcodes": "ReferralCodes",
    "referralcode-tv": "ReferralCode.tv",
    "referraldrop": "ReferralDrop",
}

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

_FIELD_COMMANDS = {
    "personal_code": "code",
    "personal_link": "lien",
    "referee_reward": "gain filleul",
    "referrer_reward": "gain parrain",
    "conditions": "conditions",
    "min_deposit": "dépôt minimum",
    "min_spend": "dépense minimum",
    "min_trade": "minimum de trade",
    "trade_min": "minimum de trade",
    "transaction_count": "nombre de transactions",
    "qualification_days": "délai",
    "expiry_date": "date d'expiration",
    "reward_type": "type de récompense",
    "title": "titre",
}

_EVENT_LABELS = {
    "real_write": "mise à jour réelle effectuée",
    "post_verify_success": "mise à jour vérifiée",
    "post_verify_failure": "vérification après écriture échouée",
    "monitor_real_safe_diff": "changement public détecté",
    "platform_status_change": "état de plateforme modifié",
    "workflow_error": "erreur d'automatisation",
    "human_required": "intervention manuelle requise",
    "rollback": "retour arrière effectué",
    "pending_created": "mise à jour mise en attente",
    "pending_closed": "mise à jour en attente clôturée",
    "circuit_breaker_open": "sécurité activée — écritures bloquées",
    "canary_real": "test réel contrôlé",
    "bump_notable": "remontée d'annonce",
    "external_blocker": "blocage externe",
}

_REASON_LABELS = {
    "cloudflare_turnstile_challenge": "challenge Cloudflare Turnstile",
    "CAPTCHA_OR_ANTIBOT": "protection anti-bot / CAPTCHA",
    "403_ANTIBOT": "accès refusé par une protection anti-bot",
    "RATE_LIMIT": "limite de requêtes atteinte",
    "AUTH_BLOCKED": "authentification requise",
    "EXPECTED_EXTERNAL_BLOCKER": "blocage externe connu",
}

_LEVEL_ICONS = {
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "HUMAN_REQUIRED": "🖐️",
}

_MAX_RICH_EVENTS = 12
_MAX_BLOCK_TEXT = 2900
_RCTV_LISTINGS_URL = "https://www.referralcode.tv/my-account/?tab=listings"


@lru_cache(maxsize=256)
def _program_label(value: object) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    try:
        offer = OffersRepository().get_by_slug(key)
        name = str((offer or {}).get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    special = {
        "igraal": "iGraal",
        "totalenergies": "TotalEnergies",
        "nrjmobile": "NRJ Mobile",
        "kraken": "Kraken",
    }
    return special.get(key.lower(), key.replace("-", " ").title())


def _label_platform(value: object) -> str:
    key = str(value or "")
    return _PLATFORM_LABELS.get(key, key or "AutoFresh")


def _field_label(value: object) -> str:
    key = str(value or "")
    return _FIELD_LABELS.get(key, key.replace("_", " ") or "valeur")


def _label_reason(value: object) -> str:
    key = str(value or "")
    return _REASON_LABELS.get(key, key.replace("_", " ").lower())


def _format_value(value: object) -> str:
    if value is None or value == "":
        return "non défini"
    return str(value)


def _compact(value: object, limit: int = 48) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _safe_command_text(text: str) -> bool:
    if not text or len(text) > 4000:
        return False
    if re.search(r"[;&|\x60$<>\\\\]", text):
        return False
    if re.search(r"\b(curl|wget|bash|powershell|cmd\.exe)\b", text, flags=re.I):
        return False
    return True


def _correlation_id(event: dict, suffix: str) -> str:
    run_id = str(event.get("run_id") or "runtime")
    program = str(event.get("program") or "global")
    field = str(event.get("field") or "event")
    return f"notify:{run_id}:{program}:{field}:{suffix}"[:180]


def _command_value(command: str, event: dict, suffix: str) -> str:
    return json.dumps(
        {
            "command": command,
            "correlation_id": _correlation_id(event, suffix),
            "origin": "notification",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )[:2000]


def _later_value(event: dict) -> str:
    return json.dumps(
        {
            "program": str(event.get("program") or ""),
            "correlation_id": _correlation_id(event, "later"),
            "origin": "notification",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )[:2000]


def _button(
    text: str,
    action_id: str,
    *,
    value: str | None = None,
    url: str | None = None,
    style: str | None = None,
    confirm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "button",
        "action_id": action_id,
        "text": {"type": "plain_text", "text": _compact(text, 70)},
    }
    if value is not None:
        out["value"] = value[:2000]
    if url is not None:
        out["url"] = url
    if style:
        out["style"] = style
    if confirm:
        out["confirm"] = confirm
    return out


def _accept_command(event: dict) -> str | None:
    program = _program_label(event.get("program"))
    field = str(event.get("field") or "")
    field_command = _FIELD_COMMANDS.get(field)
    new_value = event.get("new_value")
    if not program or not field_command or new_value is None:
        return None
    command = f"{program} {field_command} {new_value}".strip()
    return command if _safe_command_text(command) else None


def _monitor_event_text(event: dict, *, rich: bool) -> str:
    program = _program_label(event.get("program")) or "Offre"
    field = _field_label(event.get("field"))
    old = _format_value(event.get("old_value"))
    new = _format_value(event.get("new_value"))
    evidence = event.get("evidence_count")
    impact = event.get("impact_count")
    source_url = str(event.get("source_url") or "").strip()

    if not rich:
        return (
            f"🔎 {program} — changement détecté : {field} {old} → {new}. "
            "Aucune modification n'a été faite. "
            f"Boutons disponibles dans Slack ; secours : /autofresh {program} divergences"
        )

    lines = [
        f"🔎 *{program} — changement détecté*",
        f"*{field}* : `{old}` → `{new}`",
    ]
    proof = []
    try:
        if evidence is not None and int(evidence) > 0:
            proof.append(f"détecté {int(evidence)} fois de suite")
    except (TypeError, ValueError):
        pass
    try:
        if impact is not None and int(impact) > 0:
            n = int(impact)
            proof.append(f"{n} annonce{'s' if n != 1 else ''} potentiellement concernée{'s' if n != 1 else ''}")
    except (TypeError, ValueError):
        pass
    if source_url.startswith(("https://", "http://")):
        proof.append(f"<{source_url}|ouvrir la source suivie>")
    if proof:
        lines.append(" · ".join(proof))
    lines.append("_Aucune annonce n'a été modifiée._")
    return "\n".join(lines)


def _event_line(event: dict) -> str:
    """Render one runtime event for the fallback/accessibility text."""
    level = str(event.get("level") or "").upper()
    ev = str(event.get("event") or "")
    action = str(event.get("action") or "")
    result = str(event.get("result") or "")
    platform = _label_platform(event.get("platform"))
    program = _program_label(event.get("program"))

    if ev == "monitor_real_safe_diff":
        return _monitor_event_text(event, rich=False)

    if ev == "workflow_error" and action == "missed_slot_catchup":
        return (
            "⚠️ Bumper Code-Parrainage + Parrainage.co — un créneau a démarré "
            "en retard puis a été rattrapé automatiquement. Aucune action nécessaire."
        )

    if (
        ev == "workflow_error"
        and str(event.get("platform") or "") == "super-parrain"
        and action == "content_prefill"
        and result == "FAIL_CLOSED"
    ):
        subject = f"Super-Parrain / {program}" if program else "Super-Parrain"
        return (
            f"⚠️ {subject} — la mise à jour du contenu a été annulée par sécurité "
            "car deux vérifications n'étaient pas d'accord. Le bumper continue. "
            "Aucune action immédiate."
        )

    if ev == "post_verify_failure":
        subject = platform + (f" / {program}" if program else "")
        return (
            f"❌ {subject} — la vérification finale après écriture a échoué. "
            "Action requise : contrôler le statut avant toute nouvelle écriture."
        )

    if ev == "external_blocker":
        reason = _label_reason(event.get("block_reason") or event.get("result"))
        icon = _LEVEL_ICONS.get(level, "⚠️")
        return (
            f"{icon} {platform}"
            + (f" / {program}" if program else "")
            + f" — blocage externe du site ({reason}). Aucune tentative de contournement."
        )

    if ev == "post_verify_success":
        return (
            f"✅ {platform}"
            + (f" / {program}" if program else "")
            + " — mise à jour effectuée et vérifiée."
        )

    level_icon = _LEVEL_ICONS.get(level, "•")
    label = _EVENT_LABELS.get(ev, ev.replace("_", " "))
    detail = event.get("block_reason") or event.get("result")
    suffix = f" — {_label_reason(detail)}" if detail and detail != "[REDACTED]" else ""
    subject = platform + (f" / {program}" if program else "")
    return f"{level_icon} {subject} — {label}{suffix}"


def _read_command_button(label: str, command: str, event: dict, suffix: str) -> dict[str, Any] | None:
    if not _safe_command_text(command):
        return None
    return _button(
        label,
        "autofresh_command",
        value=_command_value(command, event, suffix),
    )


def _later_button(event: dict) -> dict[str, Any]:
    return _button("Plus tard", "autofresh_later", value=_later_value(event))


def _rctv_manual_button() -> dict[str, Any]:
    """Open the legitimate human-only ReferralCode.tv listings page."""
    return _button(
        "Remonter manuellement",
        "autofresh_open_rctv",
        url=_RCTV_LISTINGS_URL,
        style="primary",
    )


def _rctv_done_button(event: dict) -> dict[str, Any]:
    return _button(
        "Marquer fait",
        "autofresh_mark_done",
        value=_later_value(event),
    )


def _monitor_blocks(event: dict) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    command = _accept_command(event)
    if command:
        new = _compact(event.get("new_value"), 32)
        elements.append(
            _button(
                f"Accepter {new}",
                "autofresh_apply_candidate",
                value=_command_value(command, event, "accept"),
                style="primary",
                confirm={
                    "title": {"type": "plain_text", "text": "Accepter cette valeur ?"},
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "AutoFresh enregistrera cette nouvelle valeur. "
                            "Toute écriture immédiate sur un site compatible demandera "
                            "encore une confirmation séparée."
                        ),
                    },
                    "confirm": {"type": "plain_text", "text": "Accepter"},
                    "deny": {"type": "plain_text", "text": "Annuler"},
                },
            )
        )
    program = _program_label(event.get("program"))
    detail = _read_command_button(
        "Voir le détail",
        f"{program} divergences",
        event,
        "details",
    ) if program else None
    if detail:
        elements.append(detail)
    elements.append(_later_button(event))
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _monitor_event_text(event, rich=True)[:_MAX_BLOCK_TEXT]},
        },
        {"type": "actions", "elements": elements[:5]},
    ]


def _inspection_blocks(event: dict, text: str) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    if str(event.get("platform") or "") == "referralcode-tv":
        elements.append(_rctv_manual_button())
        elements.append(_rctv_done_button(event))
    program = _program_label(event.get("program"))
    if program:
        for label, command, suffix in (
            ("Voir le statut", f"{program} statut", "status"),
            ("Voir les divergences", f"{program} divergences", "details"),
        ):
            btn = _read_command_button(label, command, event, suffix)
            if btn:
                elements.append(btn)
    else:
        btn = _read_command_button(
            "Voir les plateformes",
            "Autofresh plateformes",
            event,
            "platforms",
        )
        if btn:
            elements.append(btn)
    elements.append(_later_button(event))
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:_MAX_BLOCK_TEXT]}}
    ]
    if elements:
        blocks.append({"type": "actions", "elements": elements[:5]})
    return blocks


def _event_blocks(event: dict) -> list[dict[str, Any]]:
    ev = str(event.get("event") or "")
    action = str(event.get("action") or "")
    result = str(event.get("result") or "")

    if ev == "monitor_real_safe_diff":
        return _monitor_blocks(event)

    if (
        ev == "workflow_error"
        and str(event.get("platform") or "") == "super-parrain"
        and action == "content_prefill"
        and result == "FAIL_CLOSED"
    ):
        program = _program_label(event.get("program"))
        subject = f"Super-Parrain / {program}" if program else "Super-Parrain"
        text = (
            f"⚠️ *{subject} — aucune action immédiate*\n"
            "Deux vérifications ne sont pas d'accord. La modification du contenu a été "
            "bloquée par sécurité. ✅ La remontée de l'annonce continue normalement."
        )
        return _inspection_blocks(event, text)

    if ev == "post_verify_failure":
        platform = _label_platform(event.get("platform"))
        program = _program_label(event.get("program"))
        subject = platform + (f" / {program}" if program else "")
        text = (
            f"❌ *{subject} — action requise*\n"
            "Une écriture a été tentée, mais la vérification finale n'a pas confirmé "
            "le résultat. Ne relance pas d'écriture avant contrôle."
        )
        return _inspection_blocks(event, text)

    if ev == "workflow_error" and action == "missed_slot_catchup":
        btn = _read_command_button("Voir les remontées", "Autofresh bump", event, "bump")
        blocks = [{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "⚠️ *Bumper — aucune action nécessaire*\n"
                    "Un créneau a démarré en retard puis a été rattrapé automatiquement."
                ),
            },
        }]
        if btn:
            blocks.append({"type": "actions", "elements": [btn]})
        return blocks

    if (
        ev in {"external_blocker", "human_required"}
        and str(event.get("platform") or "") == "referralcode-tv"
    ):
        text = (
            "🖐️ *ReferralCode.tv — remontée manuelle*\n"
            "AutoFresh ne peut pas effectuer la remontée depuis GitHub car le site "
            "présente un challenge Cloudflare Turnstile. Utilise le bouton ci-dessous "
            "pour ouvrir directement tes annonces et effectuer la remontée toi-même. "
            "Une fois fait, utilise « Marquer fait » pour fermer visuellement l'alerte. "
            "Aucun contournement du challenge n'est tenté."
        )
        return _inspection_blocks(event, text)

    if ev in {"external_blocker", "human_required", "circuit_breaker_open"}:
        return _inspection_blocks(event, _event_line(event))

    return [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": _event_line(event)[:_MAX_BLOCK_TEXT]},
    }]


def build_payload(events: list[dict], channel: str) -> dict | None:
    safe_events: list[dict] = []
    lines: list[str] = []
    for raw in events:
        level, event = raw.get("level", ""), raw.get("event", "")
        if not should_notify(event, level):
            continue
        safe = build_event(
            level,
            event,
            **{
                k: v
                for k, v in raw.items()
                if k in FIELDS and k not in {"level", "event"}
            },
        )
        safe_events.append(safe)
        lines.append(_event_line(safe)[:500])

    if not safe_events:
        return None

    text = "AutoFresh — à savoir\n" + "\n".join(lines[:40])
    if len(lines) > 40:
        text += f"\n+{len(lines) - 40} événement(s) dans l'archive du workflow."

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "AutoFresh — notifications"},
        }
    ]
    for event in safe_events[:_MAX_RICH_EVENTS]:
        blocks.extend(_event_blocks(event))
        if len(blocks) >= 46:
            break
    if len(safe_events) > _MAX_RICH_EVENTS:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"… +{len(safe_events) - _MAX_RICH_EVENTS} autre(s) événement(s) dans l'archive du workflow.",
            },
        })

    return {
        "channel": channel,
        "text": text,
        "blocks": blocks[:50],
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def deliver(payload: dict, token: str) -> bool:
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            accepted = json.loads(response.read().decode("utf-8")).get("ok") is True
    except Exception:
        accepted = False
    print("slack_notifications_delivered=" + str(accepted).lower())
    if not accepted:
        print("::warning::Slack delivery unverified; inspect the notification artifact. No automatic retry.")
    return accepted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send one labelled transport test, without platform activity",
    )
    args = parser.parse_args(argv)
    events = [] if args.test else read_events()
    if not events and not args.test:
        print("notification_outbox_empty=true")
        return 0
    channel = os.environ.get("AUTOFRESH_SLACK_CHANNEL", "").strip()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not channel or not token:
        print("::warning::Slack notification configuration missing; delivery NOT VERIFIED.")
        return 1
    payload = (
        {
            "channel": channel,
            "text": "AutoFresh — test de livraison Slack. Aucune écriture sur une plateforme.",
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if args.test
        else build_payload(events, channel)
    )
    return 0 if payload is None or deliver(payload, token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
