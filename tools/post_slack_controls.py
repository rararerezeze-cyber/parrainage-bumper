"""Publish an actionable AutoFresh Slack control panel; no site automation."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.slack_controls import command_panel, program_command_buttons
from tools.notify_slack import deliver


def build_panel(channel: str) -> dict:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "AutoFresh — panneau de commandes"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            "Toutes les actions ci-dessous correspondent aux commandes `/autofresh …`. "
            "Les consultations ne modifient aucune annonce. Pour modifier une valeur, "
            "utilise une commande avec sa nouvelle valeur puis la confirmation d'écriture séparée."
        )}},
    ]
    blocks.extend(command_panel(origin="control-panel", global_actions=True))
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": (
            "*À propos des alertes récentes*\n"
            "• Super-Parrain / TotalEnergies : la dernière attente enregistrée "
            "dans le dépôt est clôturée. Vérifie son état actuel ci-dessous.\n"
            "• Code-Parrainage : un CAPTCHA a bloqué une tentative. "
            "Ouvre le site ou consulte les plateformes ; aucun bouton ne contourne le CAPTCHA."
        )},
    })
    blocks.extend(command_panel(program="totalenergies", origin="control-total", global_actions=False))
    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "action_id": "autofresh_open_code_parrainage",
            "text": {"type": "plain_text", "text": "Ouvrir Code-Parrainage"},
            "url": "https://code-parrainage.net/",
        }],
    })
    return {
        "channel": channel,
        "text": "AutoFresh — commandes et diagnostics accessibles par boutons.",
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def main() -> int:
    channel = os.environ.get("AUTOFRESH_SLACK_CHANNEL", "").strip()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not channel or not token:
        print("::error::Missing Slack channel or token. No message sent.")
        return 1
    return 0 if deliver(build_panel(channel), token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
