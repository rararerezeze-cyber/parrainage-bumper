"""Shared logic for bump_autres.yml missed-run detection and status.

Split from any single caller on purpose: tools/bump_watchdog.py (the
hourly catch-up dispatcher) and the Slack-facing "Autofresh bump" status
meta-command both need the same "what's the state of the last few runs"
view. Network I/O (the GitHub API call) lives in `fetch_recent_runs`;
everything else here is a pure function over already-fetched run data, so
it can be tested without mocking HTTP.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

REPO = "rararerezeze-cyber/parrainage-bumper"
WORKFLOW_FILE = "bump_autres.yml"
EXPECTED_INTERVAL_HOURS = 5.0
# 5h cadence + 1h safety margin before a gap counts as "genuinely missed"
# rather than ordinary GitHub Actions scheduling jitter.
CATCHUP_THRESHOLD_HOURS = 6.0


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def fetch_recent_runs(token: str, *, per_page: int = 10) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page={per_page}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "autofresh-bump-watch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("workflow_runs") or []


def dispatch_catchup(token: str, *, ref: str = "main") -> None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        data=json.dumps({"ref": ref}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "autofresh-bump-watch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def decide_catchup(
    runs: list[dict[str, Any]],
    *,
    now: datetime,
    threshold_hours: float = CATCHUP_THRESHOLD_HOURS,
) -> dict[str, Any]:
    """Pure decision: should a catch-up dispatch happen right now?

    Never recommends a dispatch while a run is queued/in_progress (the
    workflow's own concurrency group would just serialize it anyway, but
    deciding not to dispatch at all avoids piling up redundant queued
    catch-up requests).
    """
    in_flight = [r for r in runs if r.get("status") in ("queued", "in_progress")]
    if in_flight:
        return {"action": "skip", "reason": "in_flight", "count": len(in_flight)}

    completed = [r for r in runs if r.get("status") == "completed"]
    if not completed:
        return {"action": "skip", "reason": "no_completed_runs"}

    latest = completed[0]
    created = _parse_ts(latest["created_at"])
    elapsed_hours = (now - created).total_seconds() / 3600.0

    if elapsed_hours < threshold_hours:
        return {"action": "skip", "reason": "within_threshold", "elapsed_hours": elapsed_hours}

    return {
        "action": "dispatch",
        "reason": "missed_run",
        "elapsed_hours": elapsed_hours,
        "latest_run_id": latest.get("id"),
        "latest_run_at": latest.get("created_at"),
    }


def summarize_status(runs: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    """Build the data behind the Slack-facing 'Autofresh bump' summary:
    last run, its outcome, the next expected slot, and any recent
    failures. Pure function over already-fetched run data."""
    completed = [r for r in runs if r.get("status") == "completed"]
    in_flight = [r for r in runs if r.get("status") in ("queued", "in_progress")]

    if not completed:
        return {
            "last_run_at": None,
            "last_conclusion": None,
            "next_expected_at": None,
            "in_progress": bool(in_flight),
            "recent_failures": [],
        }

    latest = completed[0]
    last_run_at = latest["created_at"]
    next_expected = _parse_ts(last_run_at) + timedelta(hours=EXPECTED_INTERVAL_HOURS)

    recent_failures = [
        {"id": r.get("id"), "created_at": r.get("created_at"), "conclusion": r.get("conclusion")}
        for r in completed[:5]
        if r.get("conclusion") not in ("success", None)
    ]

    return {
        "last_run_at": last_run_at,
        "last_conclusion": latest.get("conclusion"),
        "next_expected_at": next_expected.isoformat(),
        "in_progress": bool(in_flight),
        "recent_failures": recent_failures,
    }


def format_status_fr(status: dict[str, Any], *, now: datetime) -> str:
    """Short, curated French text for the Slack 'Autofresh bump' reply."""
    if not status.get("last_run_at"):
        return "Bump Code-Parrainage / Parrainage.co : aucun run connu."

    lines = [f"*Bump Code-Parrainage / Parrainage.co*"]
    last_at = status["last_run_at"]
    conclusion = status.get("last_conclusion") or "inconnu"
    icon = "✅" if conclusion == "success" else "❌"
    lines.append(f"{icon} Dernier run : {last_at} ({conclusion})")

    if status.get("in_progress"):
        lines.append("⏳ Un run est actuellement en cours.")
    elif status.get("next_expected_at"):
        next_at = _parse_ts(status["next_expected_at"])
        if next_at < now:
            overdue_h = (now - next_at).total_seconds() / 3600.0
            lines.append(f"⚠️ Run attendu dépassé de {overdue_h:.1f}h -- rattrapage automatique surveillé.")
        else:
            lines.append(f"🕐 Prochain run attendu : {status['next_expected_at']}")

    failures = status.get("recent_failures") or []
    if failures:
        lines.append(f"❌ {len(failures)} échec(s) récent(s) sur les 5 derniers runs.")

    return "\n".join(lines)
