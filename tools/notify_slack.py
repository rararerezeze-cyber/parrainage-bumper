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


def _event_line(event: dict) -> str:
    level = str(event.get("level") or "").upper()
    icon = _LEVEL_ICONS.get(level, "•")
    platform = _label_platform(event.get("platform"))
    label = _EVENT_LABELS.get(
        str(event.get("event") or ""),
        str(event.get("event") or "").replace("_", " "),
    )
    detail = event.get("block_reason") or event.get("result")
    suffix = f" — {_label_reason(detail)}" if detail else ""
    return f"{icon} {platform} — {label}{suffix}"


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
    text = "AutoFresh — notifications\n" + "\n".join(lines[:40])
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
                "AutoFresh — test de notification Slack. Aucune écriture sur une plateforme.",
                "mrkdwn": False, "unfurl_links": False, "unfurl_media": False}
               if args.test else build_payload(events, channel))
    return 0 if payload is None or deliver(payload, token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
