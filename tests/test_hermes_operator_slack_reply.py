"""hermes_operator.yml "Post Slack reply" step -- regression tests.

Text-slice checks in the same style as test_hermes_operator_workflow.py
(no PyYAML dependency). Covers the new, optional, generic reply_channel
input added for the Slack-native Autofresh operator interface: it must
be a strict opt-in (empty by default, legacy Hermes/Telegram dispatches
untouched), never print SLACK_BOT_TOKEN, never touch the existing
commit/push safety invariants already covered elsewhere.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "hermes_operator.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def _slice_between(start_marker: str, end_marker: str | None) -> str:
    i = TEXT.index(start_marker)
    j = TEXT.index(end_marker, i) if end_marker else len(TEXT)
    return TEXT[i:j]


def _code_only(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


SLACK_STEP = _slice_between("- name: Post Slack reply", None)
SLACK_STEP_CODE = _code_only(SLACK_STEP)


def test_reply_channel_input_defaults_to_empty():
    inputs_block = _slice_between("reply_channel:", "permissions:")
    assert 'default: ""' in inputs_block
    assert "required: false" in inputs_block


def test_slack_step_is_last_and_runs_always():
    assert TEXT.rstrip().endswith(SLACK_STEP.rstrip())
    step_header = _slice_between("- name: Post Slack reply", "env:")
    assert "if: always()" in step_header


def test_slack_step_reads_inputs_only_via_env():
    assert "SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}" in SLACK_STEP
    assert "REPLY_CHANNEL: ${{ github.event.inputs.reply_channel }}" in SLACK_STEP
    assert "HERMES_INPUT_RUN_WRITERS: ${{ github.event.inputs.run_writers }}" in SLACK_STEP
    for line in SLACK_STEP_CODE.splitlines():
        if "${{ github.event.inputs." in line:
            assert line.strip().startswith(("REPLY_CHANNEL:", "HERMES_INPUT_RUN_WRITERS:")), (
                f"raw input expression outside the env: mapping: {line!r}"
            )


def test_slack_step_is_a_strict_noop_when_reply_channel_empty():
    assert 'if [ -z "$REPLY_CHANNEL" ]' in SLACK_STEP_CODE
    body = SLACK_STEP_CODE.split('if [ -z "$REPLY_CHANNEL" ]', 1)[1].split("fi", 1)[0]
    assert "exit 0" in body


def test_slack_step_never_prints_the_bot_token():
    assert "SLACK_BOT_TOKEN" not in SLACK_STEP_CODE.replace(
        "SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}", ""
    ).replace(
        'if [ -z "$SLACK_BOT_TOKEN" ]', ""
    ).replace(
        "SLACK_BOT_TOKEN secret missing", ""
    ).replace(
        "os.environ['SLACK_BOT_TOKEN']", ""
    )
    for line in SLACK_STEP_CODE.splitlines():
        if "print(" in line:
            assert "SLACK_BOT_TOKEN" not in line
            assert "Authorization" not in line


def test_slack_step_uses_slack_format_module_not_ad_hoc_formatting():
    assert "from lib.slack_format import render_result" in SLACK_STEP_CODE


def test_slack_step_generic_not_hardcoded_per_channel():
    """One shared step driven by an input, not per-channel branches --
    exactly what the migration mandate requires (no #hermes/#betstats/
    #bonusparrain special-casing)."""
    for bad in ("#hermes", "#betstats", "#bonusparrain", "C0BT"):
        assert bad not in SLACK_STEP_CODE, f"channel-specific hardcoding found: {bad!r}"


def test_slack_step_has_no_bare_swallow_and_does_not_touch_git():
    assert "|| true" not in SLACK_STEP_CODE
    assert "git push" not in SLACK_STEP_CODE
    assert "git pull" not in SLACK_STEP_CODE
    assert "git commit" not in SLACK_STEP_CODE


def test_slack_step_handles_network_failure_without_raising():
    assert "except Exception" in SLACK_STEP_CODE
    assert "::warning::Slack post failed" in SLACK_STEP


def test_slack_step_checks_slack_api_ok_field_not_just_http_status():
    assert 'body.get("ok")' in SLACK_STEP_CODE
    assert "::warning::Slack API rejected message" in SLACK_STEP


def test_legacy_run_step_untouched_by_the_new_step():
    """The pre-existing HERMES_INPUT_* env passthrough test for the Run
    step covers this already -- this is a targeted sanity check that the
    new step is additive, not a change to that step's own env mapping."""
    run_step = _slice_between(
        "- name: Run Hermes Autofresh command", "- name: Upload Hermes JSON result"
    )
    assert "REPLY_CHANNEL" not in run_step
