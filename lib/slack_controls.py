"""Safe, reusable Slack Block Kit controls for the AutoFresh command surface.

Buttons execute the same signed, allowlisted, writer-less workflows as
/autofresh. Never place mutating commands in these navigation panels.
"""
from __future__ import annotations

import json
import re
from typing import Any

SAFE_PROGRAM = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,40}$")
GLOBAL_COMMANDS = (
    ("Commandes", "Autofresh menu", "menu"),
    ("Enseignes", "Autofresh enseignes", "offers"),
    ("Plateformes", "Autofresh plateformes", "platforms"),
    ("Remontées", "Autofresh bump", "bump"),
    ("Exemples", "Autofresh exemples", "examples"),
)
PROGRAM_COMMANDS = (
    ("Statut", "statut"),
    ("Valeurs", "valeurs"),
    ("Divergences", "divergences"),
    ("Sites associés", "plateformes"),
)


def command_button(label: str, command: str, *, origin: str, suffix: str) -> dict[str, Any]:
    return {
        "type": "button",
        "action_id": f"autofresh_command_{suffix[:100]}",
        "text": {"type": "plain_text", "text": label[:75]},
        "value": json.dumps(
            {
                "command": command,
                "correlation_id": f"{origin}:{suffix}"[:180],
                "origin": origin,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )[:2000],
    }


def global_command_buttons(*, origin: str = "controls") -> list[dict[str, Any]]:
    return [
        command_button(label, cmd, origin=origin, suffix=slug)
        for label, cmd, slug in GLOBAL_COMMANDS
    ]


def program_command_buttons(program: str, *, origin: str = "controls") -> list[dict[str, Any]]:
    key = str(program or "").lower().strip()
    if not SAFE_PROGRAM.fullmatch(key):
        return []
    return [
        command_button(label, f"{key} {action}", origin=origin, suffix=f"{key}-{action}")
        for label, action in PROGRAM_COMMANDS
    ]


def command_panel(*, program: str | None = None, origin: str = "controls", global_actions: bool = True) -> list[dict[str, Any]]:
    blocks = []
    if program and program_command_buttons(program, origin=origin):
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Actions pour l'enseigne *{program}* · consultations sans écriture"}],
        })
        blocks.append({"type": "actions", "elements": program_command_buttons(program, origin=origin)})
    if global_actions:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Commandes rapides AutoFresh · équivalentes à /autofresh … · lecture seule"}],
        })
        blocks.append({"type": "actions", "elements": global_command_buttons(origin=origin)})
    return blocks
