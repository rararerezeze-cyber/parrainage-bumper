"""Read-only Slack palette and one-time channel control panel."""
import json

from lib.slack_controls import command_panel, global_command_buttons, program_command_buttons
from tools.post_slack_controls import build_panel


def _controls(blocks):
    return [a for b in blocks if b.get("type") == "actions" for a in b["elements"]]


def test_global_commands_are_real_signed_action_buttons():
    actions = global_command_buttons(origin="test")
    assert len(actions) == 5
    assert len({a["action_id"] for a in actions}) == 5
    commands = {json.loads(a["value"])["command"] for a in actions}
    assert commands == {
        "Autofresh menu", "Autofresh enseignes", "Autofresh plateformes",
        "Autofresh bump", "Autofresh exemples",
    }
    assert all(a["action_id"].startswith("autofresh_command_") for a in actions)
    assert all("confirm" not in a for a in actions)


def test_program_buttons_reject_invalid_slugs_and_are_read_only():
    assert program_command_buttons("totalenergies")
    assert program_command_buttons("totalenergies;drop tables") == []
    buttons = program_command_buttons("totalenergies")
    commands = {json.loads(a["value"])["command"] for a in buttons}
    assert commands == {
        "totalenergies statut", "totalenergies valeurs",
        "totalenergies divergences", "totalenergies plateformes",
    }


def test_slack_panel_has_actions_for_current_incidents():
    panel = build_panel("C_TEST")
    assert panel["channel"] == "C_TEST"
    controls = _controls(panel["blocks"])
    assert len(controls) == 10  # 5 global, 4 TotalEnergies, 1 Code-Parrainage
    assert any(a.get("url") == "https://code-parrainage.net/" for a in controls)
    commands = {json.loads(a["value"])["command"] for a in controls if "value" in a}
    assert {"Autofresh menu", "Autofresh exemples",
            "totalenergies statut", "totalenergies valeurs"} <= commands
    for block in panel["blocks"]:
        if block.get("type") == "actions":
            ids = [a["action_id"] for a in block["elements"]]
            assert len(ids) <= 5
            assert len(ids) == len(set(ids))
    assert all(a.get("action_id") != "autofresh_confirm_write" for a in controls)


def test_program_context_palette_does_not_inject_mutations():
    blocks = command_panel(program="totalenergies", global_actions=False)
    buttons = _controls(blocks)
    assert len(buttons) == 4
    assert all(json.loads(a["value"])["command"].split()[-1] in {
        "statut", "valeurs", "divergences", "plateformes"
    } for a in buttons)
