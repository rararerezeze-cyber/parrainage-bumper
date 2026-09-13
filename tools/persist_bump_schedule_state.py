"""Persist bump scheduler state without losing concurrent progress.

The scheduler can be delayed by GitHub runner capacity. Two old/new wake-ups may
therefore execute close together even though the workflow has a concurrency
group. A plain git pull --rebase can then conflict on the single JSON state file.

This helper treats the schedule already committed on origin/main as the timing
authority and merges only monotonic execution progress from the current runner.
It retries a rejected push against a fresh origin/main checkout. No site access
or workflow dispatch happens here.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.bump_autres_schedule import (
    CATCHUP_TOLERANCE_MINUTES,
    STATUS_DISPATCHED,
    STATUS_SKIPPED_ELAPSED,
)

RELATIVE_PATH = Path("data/bump-autres-schedule.json")
MAX_PUSH_ATTEMPTS = 4


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _earliest_iso(*values: str | None) -> str | None:
    parsed = [dt for dt in (_parse_dt(v) for v in values) if dt is not None]
    return min(parsed).isoformat() if parsed else None


def _latest_iso(*values: str | None) -> str | None:
    parsed = [dt for dt in (_parse_dt(v) for v in values) if dt is not None]
    return max(parsed).isoformat() if parsed else None


def _slot_map(schedule: dict[str, Any]) -> dict[int, dict[str, Any]]:
    slots = schedule.get("slots") or []
    out: dict[int, dict[str, Any]] = {}
    for slot in slots:
        index = slot.get("index")
        if not isinstance(index, int) or index in out:
            raise RuntimeError("invalid schedule slot indexes")
        out[index] = slot
    return out


def merge_schedule_state(
    durable: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Merge only monotonic runner progress into the durable schedule.

    For the same UTC day, origin/main keeps ownership of generated_at and
    planned_at. Candidate state may only advance a logical slot to dispatched,
    carry the earliest actual dispatch timestamp, and advance recovery metadata.

    If two first-of-day runners generated different random schedules, whichever
    schedule reached main first remains authoritative. A candidate that actually
    dispatched a logical slot is still folded into that durable slot by index,
    because slot_id is period_date:index and is the real idempotency identity.
    """
    if durable is None:
        return copy.deepcopy(candidate)

    durable_period = str(durable.get("period_date") or "")
    candidate_period = str(candidate.get("period_date") or "")
    if not durable_period or not candidate_period:
        raise RuntimeError("schedule period_date missing")
    if durable_period != candidate_period:
        # ISO dates sort chronologically. Never let a delayed prior-day runner
        # overwrite a newer day that is already durable.
        return copy.deepcopy(candidate if candidate_period > durable_period else durable)

    if durable.get("version") != candidate.get("version"):
        raise RuntimeError("schedule version mismatch")

    merged = copy.deepcopy(durable)
    durable_slots = _slot_map(merged)
    candidate_slots = _slot_map(candidate)
    if set(durable_slots) != set(candidate_slots):
        raise RuntimeError("schedule slot set mismatch")

    for index, out_slot in durable_slots.items():
        incoming = candidate_slots[index]

        if incoming.get("status") == STATUS_DISPATCHED:
            # If the durable generator skipped this bucket but another runner
            # actually dispatched the same logical slot, preserve that truth.
            if out_slot.get("status") == STATUS_SKIPPED_ELAPSED and not out_slot.get("planned_at"):
                if not incoming.get("planned_at"):
                    raise RuntimeError("dispatched slot has no planned_at")
                out_slot["planned_at"] = incoming["planned_at"]
            out_slot["status"] = STATUS_DISPATCHED
            dispatched_at = _earliest_iso(
                out_slot.get("dispatched_at"),
                incoming.get("dispatched_at"),
            )
            if not dispatched_at:
                raise RuntimeError("dispatched slot has no dispatched_at")
            out_slot["dispatched_at"] = dispatched_at
            planned_at = _parse_dt(out_slot.get("planned_at"))
            actual_at = _parse_dt(dispatched_at)
            out_slot["catchup"] = bool(
                planned_at
                and actual_at
                and (actual_at - planned_at) > timedelta(minutes=CATCHUP_TOLERANCE_MINUTES)
            )

        durable_recovery = int(out_slot.get("recovery_count") or 0)
        incoming_recovery = int(incoming.get("recovery_count") or 0)
        recovery_count = max(durable_recovery, incoming_recovery)
        if recovery_count:
            out_slot["recovery_count"] = recovery_count
            recovery_at = _latest_iso(
                out_slot.get("recovery_dispatched_at"),
                incoming.get("recovery_dispatched_at"),
            )
            if recovery_at:
                out_slot["recovery_dispatched_at"] = recovery_at

    return merged


def _git_output(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        encoding="utf-8",
        stderr=subprocess.STDOUT,
    ).strip()


def _git_run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _durable_from_origin(root: Path) -> dict[str, Any] | None:
    try:
        raw = _git_output(root, "show", f"origin/main:{RELATIVE_PATH.as_posix()}")
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw)


def persist_schedule_state(root: Path) -> None:
    candidate_path = root / RELATIVE_PATH
    if not candidate_path.exists():
        print("schedule_state_missing=true")
        return

    candidate = _load(candidate_path)

    for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
        _git_output(root, "fetch", "origin", "main")
        durable = _durable_from_origin(root)
        merged = merge_schedule_state(durable, candidate)

        if durable is not None and merged == durable:
            print("schedule_state_already_durable=true")
            return

        with tempfile.TemporaryDirectory(prefix="autofresh-schedule-") as tmp:
            checkout = Path(tmp) / "checkout"
            added = False
            try:
                _git_output(root, "worktree", "add", "--detach", str(checkout), "origin/main")
                added = True
                _write(checkout / RELATIVE_PATH, merged)
                _git_output(checkout, "add", RELATIVE_PATH.as_posix())
                if not _git_output(checkout, "status", "--porcelain"):
                    print("schedule_state_already_durable=true")
                    return
                _git_output(
                    checkout,
                    "-c", "user.name=autofresh-bot",
                    "-c", "user.email=autofresh-bot@users.noreply.github.com",
                    "commit", "-m", "chore(bump): scheduled slot state",
                )
                pushed = _git_run(checkout, "push", "origin", "HEAD:main")
                if pushed.returncode != 0:
                    print(
                        f"::warning::schedule state push raced with main "
                        f"(attempt {attempt}/{MAX_PUSH_ATTEMPTS}); retrying"
                    )
                    continue
            finally:
                if added:
                    _git_run(root, "worktree", "remove", "--force", str(checkout))

        _git_output(root, "fetch", "origin", "main")
        verified = _durable_from_origin(root)
        if verified is None:
            raise RuntimeError("schedule state missing after push")
        if merge_schedule_state(verified, candidate) != verified:
            raise RuntimeError("schedule state post-push verification failed")
        print("schedule_state_persisted_and_verified=true")
        return

    raise RuntimeError("schedule state push kept racing with main")


def main() -> int:
    try:
        persist_schedule_state(ROOT)
    except Exception as exc:
        print(f"::error::Schedule state persistence failed ({type(exc).__name__}): {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
