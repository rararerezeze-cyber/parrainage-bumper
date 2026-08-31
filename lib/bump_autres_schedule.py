"""Persisted, randomized 5-slots-per-24h scheduler for bump_autres.yml.

Replaces a same-session prior attempt (cron "0 */5 * * *", then "7 */5 * *
*") that fixed GitHub's minute-0 congestion issue but kept a fixed daily
time pattern -- explicitly rejected: the actual requirement is 5 real
cycles per 24h at *varying*, non-predictable times, not a fixed cadence.

Modeled on the one proven randomized-timing precedent in this repo,
lib.super_parrain_schedule's persistent 0-3h jitter: generate once,
persist to disk, and never reroll a period that has already started. Git
history confirms bump_autres.yml itself never had a randomized schedule of
its own (always a fixed cron since creation) -- there is no prior
mechanism to restore for this specific workflow; the pattern being reused
here is super_parrain_schedule's *persistence* discipline, not a literal
prior implementation.

Architecture (poller decoupled from the real site-bump dispatch --
mirrors bump_super_parrain.yml's "poll frequently, decide from persisted
state" shape, generalized from a rolling cooldown to N fixed daily slots):

  bump_autres_scheduler.yml (frequent poll, every ~15 min, off-peak minute)
    -> tools/bump_autres_scheduler.py
      -> ensure_schedule_for(now): generate today's SLOTS_PER_DAY random
         slots ONCE, persisted to data/bump-autres-schedule.json; never
         rerolled once a UTC calendar day has started.
      -> due_undispatched_slots(): any planned slot whose time has passed
         and hasn't been dispatched yet. This IS "missed slot detection"
         -- a slot due 3 hours ago because the poller itself was delayed
         is picked up exactly the same way as one due 30 seconds ago.
      -> dispatch each due slot exactly once (workflow_dispatch on
         bump_autres.yml), mark it dispatched, persist.

  bump_autres.yml itself carries NO schedule trigger at all --
  workflow_dispatch only. It never reads or writes this schedule file, so
  a manual/test dispatch of it can never shift or redefine the 5 planned
  slots.

Period = UTC calendar day (matches "cycles prevus aujourd'hui" and the
explicit midnight-crossing test case). This repo has no Europe/Paris-local
time handling anywhere else -- deliberately not introduced here either
(a DST-transition edge case for no demonstrated benefit); every timestamp
in this module is UTC.

A bucket whose window has already fully elapsed relative to `now` at
generation time gets no slot for that day (see generate_slots) -- this is
what prevents a late-in-the-day first deploy, or a scheduler recovering
from a long outage, from retroactively flooding several catch-up
dispatches in one poll. It only ever reduces a single day's count below
SLOTS_PER_DAY; it never piles missed slots up across day boundaries.

EXACTLY-ONCE ACROSS THE DISPATCH CRASH WINDOW (2026-08-31 audit)
------------------------------------------------------------------
The scheduler's own sequence -- dispatch_workflow() (a real, durable
GitHub API side effect) THEN mark_dispatched()+save_schedule() THEN a
separate workflow step commits+pushes -- is not atomic. If the runner
dies anywhere after the dispatch call succeeds but before that commit
lands (crash, OOM, cancelled run, a rebase/push conflict), the schedule
on `main` still shows the slot as "planned". The next ~15-min poll would
then see it as still due and dispatch it again -- a real second site bump
for the same logical slot -- even though the FIRST dispatch already
happened for real.

Fixed at the point that actually matters (not by reordering the
scheduler, which cannot make a GitHub API call and a git push atomic no
matter the order): every dispatch carries a deterministic slot_id()
("{period_date}:{index}") as a workflow_dispatch input, and
bump_autres.yml itself checks LEDGER_PATH before doing any real site
work, skipping cleanly if that slot_id was already recorded. This makes
re-dispatching the same logical slot -- for any reason, not just this one
crash window -- a safe, verified no-op, regardless of whether the
scheduler's own state ever successfully commits.
"""
from __future__ import annotations

import json
import random
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

from lib.paths import DATA_DIR

SCHEDULE_PATH = DATA_DIR / "bump-autres-schedule.json"
# Durable exactly-once ledger, checked by bump_autres.yml itself before it
# does any real site work -- see LEDGER note below.
LEDGER_PATH = DATA_DIR / "bump-autres-dispatch-ledger.json"
LEDGER_MAX_ENTRIES = 200
SLOTS_PER_DAY = 5
# Guarantees a minimum gap of 2*BUCKET_MARGIN_MINUTES between two slots in
# adjacent buckets (the worst case: one slot at its bucket's latest
# possible time, the next at its bucket's earliest possible time) --
# 20min margin -> >=40min minimum gap, still leaving ~4h of real
# randomness range within each ~4.8h bucket.
BUCKET_MARGIN_MINUTES = 20
# A slot dispatched more than this long after its planned_at is reported
# as a "rattrapage" (catch-up) -- reporting only, never changes whether or
# when the dispatch itself happens.
CATCHUP_TOLERANCE_MINUTES = 20

REPO = "rararerezeze-cyber/parrainage-bumper"
WORKFLOW_FILE = "bump_autres.yml"

STATUS_PLANNED = "planned"
STATUS_DISPATCHED = "dispatched"
STATUS_SKIPPED_ELAPSED = "skipped_period_already_elapsed"


def _day_bounds(period_date: date) -> tuple[datetime, datetime]:
    start = datetime(period_date.year, period_date.month, period_date.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _bucket_bounds(period_date: date, n: int = SLOTS_PER_DAY) -> list[tuple[datetime, datetime]]:
    start, end = _day_bounds(period_date)
    total = (end - start) / n
    return [(start + total * i, start + total * (i + 1)) for i in range(n)]


def generate_slots(
    period_date: date,
    *,
    now: datetime,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """One randomized time per bucket, buckets spanning the full UTC day
    split into SLOTS_PER_DAY equal windows -- guarantees a reasonable
    spread (no two slots can land in the same ~4.8h window) while the
    offset within each window is genuinely random, so the exact times
    vary day to day rather than forming a fixed pattern.
    """
    rng = rng or random.Random()
    slots: list[dict[str, Any]] = []
    for i, (b_start, b_end) in enumerate(_bucket_bounds(period_date)):
        if now >= b_end:
            slots.append(
                {
                    "index": i,
                    "planned_at": None,
                    "status": STATUS_SKIPPED_ELAPSED,
                    "dispatched_at": None,
                    "catchup": False,
                }
            )
            continue
        margin = timedelta(minutes=BUCKET_MARGIN_MINUTES)
        window_start = max(b_start, now) + margin
        window_end = b_end - margin
        if window_end <= window_start:
            # `now` lands right at the bucket's tail -- still give it a
            # real, immediate slot rather than silently dropping it.
            window_start = max(b_start, now)
            window_end = b_end
        span_seconds = max((window_end - window_start).total_seconds(), 0.0)
        offset = rng.uniform(0, span_seconds) if span_seconds > 0 else 0.0
        planned_at = window_start + timedelta(seconds=offset)
        slots.append(
            {
                "index": i,
                "planned_at": planned_at.isoformat(),
                "status": STATUS_PLANNED,
                "dispatched_at": None,
                "catchup": False,
            }
        )
    return slots


def _load_raw() -> dict[str, Any] | None:
    if not SCHEDULE_PATH.exists():
        return None
    try:
        return json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_schedule(data: dict[str, Any]) -> None:
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_PATH.with_suffix(SCHEDULE_PATH.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(SCHEDULE_PATH)


def ensure_schedule_for(now: datetime, *, rng: random.Random | None = None) -> dict[str, Any]:
    """Return today's schedule, generating it once if this is the first
    call for this UTC calendar date. Never rerolls an already-started
    period -- the entire point of persisting it (2026-08-31 requirement:
    "ne jamais reroll les slots d'une periode deja commencee").
    """
    period = now.date().isoformat()
    existing = _load_raw()
    if existing and existing.get("period_date") == period:
        return existing
    data = {
        "version": 1,
        "period_date": period,
        "generated_at": now.isoformat(),
        "slots": generate_slots(now.date(), now=now, rng=rng),
    }
    save_schedule(data)
    return data


def due_undispatched_slots(schedule: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    out = []
    for slot in schedule.get("slots") or []:
        if slot.get("status") != STATUS_PLANNED:
            continue
        planned_raw = slot.get("planned_at")
        if not planned_raw:
            continue
        planned_at = datetime.fromisoformat(planned_raw)
        if planned_at <= now:
            out.append(slot)
    return sorted(out, key=lambda s: s["planned_at"])


def mark_dispatched(schedule: dict[str, Any], index: int, *, now: datetime) -> dict[str, Any]:
    for slot in schedule.get("slots") or []:
        if slot.get("index") == index:
            planned_at = datetime.fromisoformat(slot["planned_at"])
            slot["status"] = STATUS_DISPATCHED
            slot["dispatched_at"] = now.isoformat()
            slot["catchup"] = (now - planned_at) > timedelta(minutes=CATCHUP_TOLERANCE_MINUTES)
            break
    return schedule


def slot_id(period_date: str, index: int) -> str:
    """Deterministic identifier for one logical daily slot -- derived
    purely from already-persisted data (no new randomness/timing), stable
    across any number of re-dispatches of the same slot."""
    return f"{period_date}:{index}"


def _load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {"version": 1, "dispatched_slot_ids": []}
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("dispatched_slot_ids"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "dispatched_slot_ids": []}


def _save_ledger(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LEDGER_PATH)


def is_slot_already_processed(sid: str) -> bool:
    """True once this exact logical slot has already run a real bump
    cycle -- checked by bump_autres.yml itself before touching any site,
    so a re-dispatch of the same slot (crash-window retry, or any other
    cause) is always a safe, verified no-op."""
    if not sid:
        return False
    return sid in (_load_ledger().get("dispatched_slot_ids") or [])


def record_slot_processed(sid: str) -> dict[str, Any]:
    data = _load_ledger()
    ids = data.setdefault("dispatched_slot_ids", [])
    if sid not in ids:
        ids.append(sid)
    if len(ids) > LEDGER_MAX_ENTRIES:
        data["dispatched_slot_ids"] = ids[-LEDGER_MAX_ENTRIES:]
    _save_ledger(data)
    return data


def dispatch_workflow(token: str, *, ref: str = "main", slot_id: str | None = None) -> None:
    body: dict[str, Any] = {"ref": ref}
    if slot_id:
        body["inputs"] = {"slot_id": slot_id}
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "autofresh-bump-scheduler",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def fetch_last_run(token: str) -> dict[str, Any] | None:
    """Light cross-check for the Slack 'erreur eventuelle' line -- the
    most recent bump_autres.yml run's own conclusion. Best-effort by
    design (see callers): must never block or fail schedule-driven
    dispatch on its own account.
    """
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=1",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "autofresh-bump-scheduler",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    runs = body.get("workflow_runs") or []
    return runs[0] if runs else None


def summarize(schedule: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Pure summary over an already-loaded schedule -- what the Slack
    'Autofresh bump' status is built from."""
    slots = schedule.get("slots") or []
    plannable = [s for s in slots if s.get("status") in (STATUS_PLANNED, STATUS_DISPATCHED)]
    done = [s for s in slots if s.get("status") == STATUS_DISPATCHED]
    pending = sorted(
        (s for s in slots if s.get("status") == STATUS_PLANNED),
        key=lambda s: s["planned_at"],
    )
    next_slot = pending[0] if pending else None
    last_done = max(done, key=lambda s: s["dispatched_at"]) if done else None
    return {
        "period_date": schedule.get("period_date"),
        "cycles_planned": len(plannable),
        "cycles_done": len(done),
        "next_planned_at": next_slot["planned_at"] if next_slot else None,
        "last_dispatched_at": last_done["dispatched_at"] if last_done else None,
        "any_catchup": any(s.get("catchup") for s in done),
    }


def format_bump_status_fr(summary: dict[str, Any], *, last_run: dict[str, Any] | None) -> str:
    lines = ["*Bumper Code-Parrainage / Parrainage.co*"]
    lines.append(f"• cycles prévus aujourd'hui : {summary['cycles_planned']}")
    lines.append(f"• cycles réalisés : {summary['cycles_done']}/{summary['cycles_planned']}")
    if summary.get("next_planned_at"):
        lines.append(f"• prochain passage prévu : {summary['next_planned_at']} (planning aléatoire)")
    else:
        lines.append("• prochain passage prévu : plus de créneau aujourd'hui (nouveau planning demain)")
    lines.append(
        f"• dernier passage : {summary['last_dispatched_at']}"
        if summary.get("last_dispatched_at")
        else "• dernier passage : aucun aujourd'hui"
    )
    lines.append(f"• rattrapage : {'oui' if summary.get('any_catchup') else 'non'}")
    if last_run and last_run.get("conclusion") not in (None, "success"):
        lines.append(f"• erreur : dernier run GitHub Actions = {last_run.get('conclusion')}")
    return "\n".join(lines)
