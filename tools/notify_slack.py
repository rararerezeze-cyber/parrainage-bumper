"""Deliver the sanitized runtime outbox to Slack without a local relay.

No retries: a timeout may mean Slack accepted the message. The artifact remains
the evidence source. Delivery errors are explicit but never undo business work.
"""
from __future__ import annotations

import json
import argparse
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.notify import FIELDS, read_events, build_event, should_notify


_PLATFORM_LABELS = {
    "super-parrain": "Super-Parrain",
    "parrainage-co": "Parrainage.co",
    "code-parrainage": "Code-Parrainage",
    "1parrainage": "1Parrainage",
    "referralcodes": "ReferralCodes",
    "referralcode-tv": "ReferralCode.tv",
    "referraldrop": "ReferralDrop",
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


def _label_platform(value: object) -> str:
    key = str(value or "")
    return _PLATFORM_LABELS.get(key, key or "AutoFresh")


def _label_reason(value: object) -> str:
    key = str(value or "")
    return _REASON_LABELS.get(key, key.replace("_", " ").lower())


def _program_label(value: object) -> str:
    text = str(value or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def _event_line(event: dict) -> str:
    """Render one runtime event for a non-technical Slack operator.

    Known events that are safe/non-actionable get an explicit explanation
    instead of exposing backend vocabulary such as FAIL_CLOSED, candidate,
    missed_slot or a redacted internal reason.
    """
    level = str(event.get("level") or "").upper()
    ev = str(event.get("event") or "")
    action = str(event.get("action") or "")
    result = str(event.get("result") or "")
    platform = _label_platform(event.get("platform"))
    program = _program_label(event.get("program"))

    if ev == "monitor_real_safe_diff":
        target = program or platform
        if program:
            return (
                f"🔎 {target} — une différence a été détectée sur l'offre publique. "
                f"Aucune modification n'a été faite. Pour voir le détail : "
                f"/autofresh {program} divergences"
            )
        return (
            f"🔎 {target} — une différence a été détectée sur une offre publique. "
            "Aucune modification n'a été faite."
        )

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
            "car deux vérifications n'étaient pas d'accord. Le bumper continue "
            "sans modifier le contenu."
        )

    if ev == "external_blocker":
        reason = _label_reason(event.get("block_reason") or event.get("result"))
        return (
            f"⚠️ {platform}"
            + (f" / {program}" if program else "")
            + f" — action bloquée par le site ({reason}). Aucune tentative de contournement."
        )

    if ev == "post_verify_success":
        return (
            f"✅ {platform}"
            + (f" / {program}" if program else "")
            + " — mise à jour effectuée et vérifiée."
        )

    if ev == "post_verify_failure":
        return (
            f"❌ {platform}"
            + (f" / {program}" if program else "")
            + " — une mise à jour a été tentée mais la vérification finale a échoué. "
            "Ne relance pas l'écriture sans contrôle."
        )

    level_icon = _LEVEL_ICONS.get(level, "•")
    label = _EVENT_LABELS.get(ev, ev.replace("_", " "))
    detail = event.get("block_reason") or event.get("result")
    suffix = f" — {_label_reason(detail)}" if detail and detail != "[REDACTED]" else ""
    subject = platform + (f" / {program}" if program else "")
    return f"{level_icon} {subject} — {label}{suffix}"


def build_payload(events: list[dict], channel: str) -> dict | None:
    lines = []
    for raw in events:
        level, event = raw.get("level", ""), raw.get("event", "")
        if not should_notify(event, level):
            continue
        safe = build_event(level, event, **{
            k: v
            for k, v in raw.items()
            if k in FIELDS and k not in {"level", "event"}
        })
        lines.append(_event_line(safe)[:500])
    if not lines:
        return None
    text = "AutoFresh — à savoir\n" + "\n".join(lines[:40])
    if len(lines) > 40:
        text += f"\n+{len(lines) - 40} événement(s) dans l'archive du workflow."
    return {
        "channel": channel,
        "text": text,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def deliver(payload: dict, token: str) -> bool:
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
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
    parser.add_argument("--test", action="store_true", help="Send one labelled transport test, without platform activity")
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
    payload = ({"channel": channel, "text":
                "AutoFresh — test de livraison Slack. Aucune écriture sur une plateforme.",
                "mrkdwn": False, "unfurl_links": False, "unfurl_media": False}
               if args.test else build_payload(events, channel))
    return 0 if payload is None or deliver(payload, token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
