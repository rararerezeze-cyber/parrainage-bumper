"""bump_autres.yml scheduling regression test.

Real incident (2026-08-31 audit): cron "0 */5 * * *" fires exactly at
minute 0 of every 5th hour -- GitHub's own docs warn scheduled workflows
are delayed or dropped under high load "especially at the start of every
hour". Confirmed live: the 05:00 and 10:00 UTC slots that day never fired
at all (GitHub Actions run history showed no runs between 02:14 and
11:11 UTC). Plain text check, not a YAML parser, matching this repo's
existing convention (see tests/test_hermes_operator_workflow.py).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bump_autres.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")

_CRON_RE = re.compile(r'cron:\s*"([^"]+)"')


def test_cron_does_not_fire_on_minute_zero():
    match = _CRON_RE.search(TEXT)
    assert match, "no cron schedule found in bump_autres.yml"
    minute_field = match.group(1).split()[0]
    assert minute_field != "0", (
        "cron minute-0 is the worst-case GitHub Actions congestion slot "
        "(documented by GitHub, confirmed live 2026-08-31 -- two "
        "consecutive daily slots silently never fired). Use a non-zero "
        "minute."
    )


def test_cron_still_targets_five_hour_cadence():
    match = _CRON_RE.search(TEXT)
    assert match.group(1).split()[1] == "*/5", (
        "the 5x/day cadence (every 5 hours) must be preserved -- only the "
        "minute offset should change"
    )


def test_workflow_dispatch_still_available_for_manual_and_catchup_runs():
    assert "workflow_dispatch:" in TEXT


def test_concurrency_group_still_prevents_overlap():
    """Load-bearing for the watchdog's safety: a legitimate scheduled run
    and a catch-up dispatch landing close together must never run
    concurrently (double-bump risk)."""
    assert "group: parrainage-bumper-autres" in TEXT
    assert "cancel-in-progress: false" in TEXT
