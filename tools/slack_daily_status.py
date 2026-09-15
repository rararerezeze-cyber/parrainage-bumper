#!/usr/bin/env python3
"""Daily read-only Slack dashboard for AutoFresh."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
SCHEDULE_PATH = DATA_DIR / "bump-autres-schedule.json"
LEDGER_PATH = DATA_DIR / "bump-autres-dispatch-ledger.json"
MONITOR_PATH = DATA_DIR / "captures" / "monitor-last-report.json"
CAPTURE_PATH = DATA_DIR / "captures" / "slack-daily-status.json"
RCTV_LISTINGS_URL = "https://www.referralcode.tv/my-account/?tab=listings"
PARIS = ZoneInfo("Europe/Paris")
CATCHUP_TOLERANCE = timedelta(minutes=20)

FIELD_LABELS = {
    "personal_code": "code de parrainage",
    "personal_link": "lien de parrainage",
    "referee_reward": "gain filleul",
    "referrer_reward": "gain parrain",
    "conditions": "conditions",
    "min_deposit": "dépôt minimum",
    "min_spend": "dépense minimum",
    "trade_min": "minimum de trade",
    "qualification_days": "délai",
    "expiry_date": "expiration",
    "reward_type": "type de récompense",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _paris_time(value: datetime | None) -> str:
    return value.astimezone(PARIS).strftime("%H:%M") if value else "—"


def _program_label(program: str) -> str:
    special = {
        "igraal": "iGraal",
        "totalenergies": "TotalEnergies",
        "nrjmobile": "NRJ Mobile",
        "traderepublic": "Trade Republic",
        "boursobank": "BoursoBank",
        "kraken": "Kraken",
    }
    return special.get(program.lower(), program.replace("-", " ").title())


def _command_value(command: str, suffix: str) -> str:
    return json.dumps(
        {"command": command, "correlation_id": f"daily:{suffix}", "origin": "daily_status"},
        ensure_ascii=False,
        separators=(",", ":"),
    )[:2000]


def _command_button(label: str, command: str, suffix: str) -> dict[str, Any]:
    return {
        "type": "button",
        "action_id": "autofresh_command",
        "text": {"type": "plain_text", "text": label[:75]},
        "value": _command_value(command, suffix),
    }


def _rctv_button(label: str = "Remonter ReferralCode.tv") -> dict[str, Any]:
    return {
        "type": "button",
        "style": "primary",
        "action_id": "autofresh_open_rctv",
        "text": {"type": "plain_text", "text": label[:75]},
        "url": RCTV_LISTINGS_URL,
    }


def filter_actionable_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide a candidate once the effective accepted/operator value already matches it."""
    try:
        from lib.operator_overrides import OperatorOverrideStore, resolve_effective_value
        store = OperatorOverrideStore()
    except Exception:
        return list(candidates or [])

    out = []
    for item in candidates or []:
        program = str(item.get("program") or "").strip().lower()
        field = str(item.get("field") or "").strip()
        observed = item.get("observed")
        if not program or not field or observed is None:
            out.append(item)
            continue
        try:
            effective = resolve_effective_value(
                program,
                field,
                canonical=None if item.get("canonical") is None else str(item.get("canonical")),
                store=store,
            )
            if str(effective.value or "").strip() == str(observed).strip():
                continue
        except Exception:
            pass
        out.append(item)
    return out


def build_dashboard_data(*, now: datetime, schedule: dict[str, Any], ledger: dict[str, Any],
                         candidates: list[dict[str, Any]], super_info: dict[str, Any]) -> dict[str, Any]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    period = str(schedule.get("period_date") or "")
    slots = schedule.get("slots") or []
    current_period = period == now.date().isoformat()
    due_slots, future_slots = [], []
    overdue_slot_ids: set[str] = set()

    if current_period:
        for slot in slots:
            planned = _dt(slot.get("planned_at"))
            if planned is None:
                continue
            slot_id = f"{period}:{int(slot.get('index', 0))}"
            if planned <= now:
                due_slots.append(slot)
                if planned + CATCHUP_TOLERANCE < now:
                    overdue_slot_ids.add(slot_id)
            else:
                future_slots.append(slot)

    completions = ledger.get("site_completions") or {}
    due_ids = {f"{period}:{int(s.get('index', 0))}" for s in due_slots} if current_period else set()
    site_rows = {}
    for site in ("code", "parrainage"):
        site_rows[site] = {
            "done_due": sum(1 for sid in due_ids if site in (completions.get(sid) or [])),
            "due": len(due_ids),
            "future": len(future_slots),
            "overdue_missing": sum(1 for sid in overdue_slot_ids if site not in (completions.get(sid) or [])),
        }

    next_slot = min((_dt(s.get("planned_at")) for s in future_slots), default=None) if future_slots else None
    actionable = list(candidates or [])
    action_required = bool(actionable) or any(r["overdue_missing"] > 0 for r in site_rows.values())

    super_next = _dt(super_info.get("next_at"))
    super_last = _dt(super_info.get("last_at"))
    super_eligible = bool(super_info.get("eligible"))
    if super_eligible and super_next and now - super_next > timedelta(hours=4):
        super_health = "late"
        action_required = True
    elif super_eligible:
        super_health = "eligible"
    elif super_last:
        super_health = "waiting"
    else:
        super_health = "unknown"
        action_required = True

    return {
        "generated_at": now.isoformat(),
        "period": period,
        "current_period": current_period,
        "total_slots": len(slots) if current_period else 0,
        "due_slots": len(due_ids),
        "future_slots": len(future_slots),
        "next_slot": next_slot.isoformat() if next_slot else None,
        "sites": site_rows,
        "candidates": actionable,
        "super": {
            **super_info,
            "last_at": super_last.isoformat() if super_last else None,
            "next_at": super_next.isoformat() if super_next else None,
            "health": super_health,
        },
        "action_required": action_required,
    }


def _site_line(label: str, row: dict[str, Any]) -> str:
    due = int(row.get("due") or 0)
    done = int(row.get("done_due") or 0)
    future = int(row.get("future") or 0)
    overdue = int(row.get("overdue_missing") or 0)
    if overdue:
        icon, tail = "⚠️", f" · {overdue} créneau{'x' if overdue > 1 else ''} en retard"
    elif done == due:
        icon, tail = "✅", ""
    else:
        icon, tail = "🕒", " · rattrapage encore dans la tolérance"
    return f"{icon} *{label}* — {done}/{due} créneaux dus réussis" + (f" · {future} à venir" if future else "") + tail


def build_payload(data: dict[str, Any], channel: str) -> dict[str, Any]:
    now = _dt(data.get("generated_at")) or datetime.now(timezone.utc)
    sites = data.get("sites") or {}
    candidates = data.get("candidates") or []
    super_info = data.get("super") or {}
    lines = [
        _site_line("Code-Parrainage", sites.get("code") or {}),
        _site_line("Parrainage.co", sites.get("parrainage") or {}),
    ]
    next_slot = _dt(data.get("next_slot"))
    if next_slot:
        lines.append(f"🗓️ Prochain créneau aléatoire : {_paris_time(next_slot)} (Paris)")

    health = str(super_info.get("health") or "")
    last_at = _dt(super_info.get("last_at"))
    next_at = _dt(super_info.get("next_at"))
    if health == "late":
        lines.append(f"⚠️ *Super-Parrain* — éligible depuis {_paris_time(next_at)} ; cycle anormalement en retard")
    elif health == "eligible":
        lines.append(f"🕒 *Super-Parrain* — éligible depuis {_paris_time(next_at)} ; le prochain passage automatique s'en charge")
    elif health == "waiting":
        lines.append(f"✅ *Super-Parrain* — dernier cycle {_paris_time(last_at)} · prochaine éligibilité {_paris_time(next_at)}")
    else:
        lines.append("⚠️ *Super-Parrain* — aucun cycle réussi enregistré")

    lines.append("🖐️ *ReferralCode.tv* — remontée manuelle disponible (Turnstile)")
    if candidates:
        lines.append(f"🔎 *Offres publiques* — {len(candidates)} changement" + ("s" if len(candidates) != 1 else "") + " encore à traiter")
        for item in candidates[:4]:
            program = _program_label(str(item.get("program") or ""))
            field = FIELD_LABELS.get(str(item.get("field") or ""), str(item.get("field") or "valeur"))
            old = "non défini" if item.get("canonical") is None else str(item.get("canonical"))
            new = "non défini" if item.get("observed") is None else str(item.get("observed"))
            lines.append(f"• {program} — {field} : {old} → {new}")
    else:
        lines.append("✅ *Offres publiques* — aucun changement confirmé en attente")

    if data.get("action_required"):
        state, header = "⚠️ Une vérification est utile — utilise les boutons ci-dessous.", "⚠️ AutoFresh — point du jour"
    else:
        state, header = "✅ Rien d'urgent. AutoFresh continue automatiquement.", "✅ AutoFresh — point du jour"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{state} · mis à jour {_paris_time(now)} (Paris) · lecture seule"}]},
    ]

    actions = [
        _command_button("Voir les remontées", "Autofresh bump", "bump"),
        _rctv_button(),
    ]
    seen: set[str] = set()
    for item in candidates:
        program = str(item.get("program") or "").strip()
        if not program or program in seen:
            continue
        seen.add(program)
        label = _program_label(program)
        actions.append(_command_button(f"Voir {label}", f"{label} divergences", f"divergences:{program}"))
        if len(actions) >= 5:
            break
    blocks.append({"type": "actions", "elements": actions[:5]})

    return {
        "channel": channel,
        "text": "AutoFresh — point du jour : " + ("vérification utile" if data.get("action_required") else "rien d'urgent"),
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def runtime_data(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    schedule = _load_json(SCHEDULE_PATH, {})
    ledger = _load_json(LEDGER_PATH, {})
    monitor = _load_json(MONITOR_PATH, {})
    candidates = filter_actionable_candidates(monitor.get("candidates") or [])
    try:
        from lib.super_parrain_schedule import current_jitter_minutes, is_eligible, last_super_action_at
        eligible, next_at, _ = is_eligible(now)
        last_at = last_super_action_at()
        super_info = {
            "eligible": eligible,
            "next_at": next_at.isoformat(),
            "last_at": last_at.isoformat() if last_at else None,
            "jitter_minutes": current_jitter_minutes(),
        }
    except Exception:
        super_info = {"eligible": False, "next_at": None, "last_at": None, "jitter_minutes": None}
    return build_dashboard_data(now=now, schedule=schedule, ledger=ledger, candidates=candidates, super_info=super_info)


def deliver(payload: dict[str, Any], token: str) -> bool:
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoFresh daily Slack dashboard")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    data = runtime_data()
    channel = os.environ.get("AUTOFRESH_SLACK_CHANNEL", "").strip() or "DRY_RUN"
    payload = build_payload(data, channel)
    CAPTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_PATH.write_text(json.dumps({"data": data, "payload": payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    real_channel = os.environ.get("AUTOFRESH_SLACK_CHANNEL", "").strip()
    if not token or not real_channel:
        print("::error::Slack daily status configuration missing")
        return 1
    ok = deliver(payload, token)
    print("slack_daily_status_delivered=" + str(ok).lower())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
