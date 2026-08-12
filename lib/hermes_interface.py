"""Stable Hermes → Autofresh operator interface.

Architecture:
  Telegram → Hermes (owns bot) → Autofresh (this module / GH Actions) → JSON → Hermes → Telegram

Autofresh does NOT own a Telegram bot. Auth is Hermes/GitHub shared token, not BotFather.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.offers import OffersRepository
from lib.operator_overrides import OperatorOverrideStore
from lib.operator_plan import format_plan_report, plan_program_impact
from lib.paths import DATA_DIR, OPERATOR_OVERRIDES_PATH
from lib.write_status import summary as write_summary

# Import command pipeline without circular CLI issues
from tools.telegram_update import apply_operator_command, parse_message

RESULT_PATH = DATA_DIR / "captures" / "hermes-last-result.json"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def authenticate_requester(requester: dict[str, Any] | None) -> dict[str, Any]:
    """Authenticate Hermes (or local operator) call.

    Accepted when:
    - AUTOFRESH_OPERATOR_TOKEN / HERMES_SHARED_TOKEN matches requester.token, OR
    - GITHUB_ACTIONS=true (workflow already authenticated by GitHub), OR
    - AUTOFRESH_ALLOW_LOCAL_OPERATOR=1 for local CLI/dev only
    """
    requester = requester or {}
    source = str(requester.get("source") or "").strip().lower()
    identity = str(requester.get("identity") or requester.get("id") or "").strip()
    token = str(requester.get("token") or "").strip()

    expected = (
        os.environ.get("AUTOFRESH_OPERATOR_TOKEN")
        or os.environ.get("HERMES_SHARED_TOKEN")
        or ""
    ).strip()
    in_gha = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    allow_local = os.environ.get("AUTOFRESH_ALLOW_LOCAL_OPERATOR", "") == "1"

    if expected:
        if not token or token != expected:
            return {
                "ok": False,
                "error": "unauthorized",
                "detail": "missing_or_invalid_operator_token",
            }
        return {
            "ok": True,
            "source": source or "hermes",
            "identity": identity or "token-auth",
            "auth": "shared_token",
        }

    if in_gha:
        # Dispatch was already authorized by GitHub credentials
        return {
            "ok": True,
            "source": source or "github_actions",
            "identity": identity or "gha",
            "auth": "github_actions",
        }

    if allow_local:
        return {
            "ok": True,
            "source": source or "local",
            "identity": identity or "local",
            "auth": "local_dev",
        }

    return {
        "ok": False,
        "error": "unauthorized",
        "detail": (
            "Set AUTOFRESH_OPERATOR_TOKEN (or HERMES_SHARED_TOKEN) for Hermes calls, "
            "or run via GitHub Actions, or AUTOFRESH_ALLOW_LOCAL_OPERATOR=1 for local tests."
        ),
    }


def _idempotency_key(command: str, parsed: dict | None, result: dict | None) -> str:
    raw = json.dumps(
        {
            "command": command,
            "action": (parsed or {}).get("action"),
            "program": (parsed or {}).get("program"),
            "platform": (parsed or {}).get("platform"),
            "field": (parsed or {}).get("field"),
            "value": (parsed or {}).get("value") or (result or {}).get("new_effective"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def run_autofresh_command(
    command: str,
    *,
    requester: dict[str, Any] | None = None,
    persist: bool = True,
    plan: bool = True,
    run_writers: bool = True,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Execute one natural-language operator command; return machine-readable JSON."""
    auth = authenticate_requester(requester)
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "autofresh",
        "command": command,
        "correlation_id": correlation_id,
        "at": _now(),
        "monitor": "OBSERVATION_ONLY",
        "mode": "HERMES_OPERATOR",
        "errors": [],
    }
    if not auth.get("ok"):
        base["errors"].append(
            {"code": auth.get("error") or "unauthorized", "detail": auth.get("detail")}
        )
        base["auth"] = auth
        return base

    base["auth"] = {
        "ok": True,
        "source": auth.get("source"),
        "identity": auth.get("identity"),
        "method": auth.get("auth"),
    }

    command = (command or "").strip()
    if not command:
        base["errors"].append({"code": "empty_command", "detail": "command required"})
        return base
    if len(command) > 4000 or "\x00" in command:
        base["errors"].append({"code": "invalid_command", "detail": "command rejected"})
        return base

    offers = OffersRepository()
    try:
        parsed = parse_message(command, offers)
    except ValueError as exc:
        base["errors"].append({"code": "parse_error", "detail": str(exc)})
        base["parsed"] = None
        return base

    base["parsed"] = parsed

    if not persist and parsed.get("action") in {"set", "remove"}:
        # dry parse + plan only
        plan_data = (
            plan_program_impact(
                parsed["program"], platform_filter=parsed.get("platform")
            )
            if plan and parsed.get("program")
            else {}
        )
        base["ok"] = True
        base["result"] = {"action": "dry_run", "note": "persist=false"}
        base["plan"] = plan_data
        base["platforms"] = _platform_rows(plan_data)
        base["write_status"] = write_summary()
        base["human_summary"] = format_plan_report(
            parsed.get("program") or "",
            parsed.get("field"),
            None,
            None,
            plan_data,
            action=parsed.get("action") or "status",
            platform=parsed.get("platform"),
        )
        base["idempotency_key"] = _idempotency_key(command, parsed, None)
        return base

    try:
        result = apply_operator_command(parsed, message=command)
    except ValueError as exc:
        base["errors"].append({"code": "apply_error", "detail": str(exc)})
        return base
    except KeyError as exc:
        base["errors"].append({"code": "unknown_program", "detail": str(exc)})
        return base

    base["result"] = result

    # Idempotence note for set with same value
    if (
        parsed.get("action") == "set"
        and result.get("old_effective") is not None
        and result.get("old_effective") == result.get("new_effective")
        and result.get("old_source") == result.get("new_source")
    ):
        base["idempotent"] = True
        base["result"]["note"] = "value_already_effective"

    plan_data: dict[str, Any] = {}
    if plan and parsed.get("program"):
        plan_data = plan_program_impact(
            parsed["program"], platform_filter=parsed.get("platform")
        )
    base["plan"] = plan_data
    base["platforms"] = _platform_rows(plan_data)
    base["write_status"] = write_summary()

    writers_report: dict[str, Any] | None = None
    if run_writers and parsed.get("action") == "set":
        try:
            import subprocess
            import sys

            root = Path(__file__).resolve().parents[1]
            proc = subprocess.run(
                [
                    sys.executable,
                    str(root / "tools" / "run_verified_writers.py"),
                    "--from-telegram",
                    "--program",
                    parsed["program"],
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=600,
            )
            wr_path = DATA_DIR / "captures" / "verified-writers-report.json"
            if wr_path.exists():
                writers_report = json.loads(wr_path.read_text(encoding="utf-8"))
            else:
                writers_report = {
                    "stdout": (proc.stdout or "")[-2000:],
                    "returncode": proc.returncode,
                }
        except Exception as exc:  # noqa: BLE001
            writers_report = {"error": str(exc)}
    base["writers"] = writers_report

    # Super-Parrain pending enqueue (content update when eligible)
    try:
        from lib.super_parrain_schedule import enqueue_pending

        if parsed.get("program") and parsed.get("action") == "set":
            if parsed.get("platform") in (None, "super-parrain"):
                enqueue_pending(
                    "super-parrain",
                    parsed["program"],
                    "fr",
                    reason=f"hermes_{parsed.get('field')}",
                )
    except Exception:
        pass

    human = format_plan_report(
        parsed.get("program") or "",
        parsed.get("field"),
        result.get("old_effective") if isinstance(result, dict) else None,
        result.get("new_effective") if isinstance(result, dict) else None,
        plan_data,
        action=parsed.get("action") or "status",
        platform=parsed.get("platform"),
        source=result.get("new_source") if isinstance(result, dict) else None,
    )
    if parsed.get("action") == "status" and isinstance(result, dict):
        human += "\n\n" + json.dumps(result.get("status"), ensure_ascii=False, indent=2)
    base["human_summary"] = human
    base["idempotency_key"] = _idempotency_key(command, parsed, result)
    base["ok"] = len(base["errors"]) == 0
    base["artifacts"] = {
        "overrides_path": str(OPERATOR_OVERRIDES_PATH),
        "result_path": str(RESULT_PATH),
    }
    return base


def _platform_rows(plan_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in plan_data.get("platforms") or []:
        rows.append(
            {
                "platform": p.get("platform"),
                "status": p.get("status"),
                "write_mode": p.get("write_mode"),
                "can_auto_write": p.get("can_auto_write"),
                "changed_fields": p.get("changed_fields") or {},
                "error": p.get("error"),
            }
        )
    # Ensure all known platforms appear via write_status if missing from plan
    if not rows:
        for p in write_summary().get("platforms") or []:
            rows.append(
                {
                    "platform": p.get("platform"),
                    "status": p.get("status"),
                    "write_mode": p.get("status"),
                    "can_auto_write": p.get("telegram_action") == "LIVE_UPDATE",
                    "changed_fields": {},
                    "telegram_action": p.get("telegram_action"),
                }
            )
    return rows


def save_result(payload: dict[str, Any], path: Path | None = None) -> Path:
    p = path or RESULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def handle_request(body: dict[str, Any]) -> dict[str, Any]:
    """Process Hermes request object.

    Expected:
      {
        "action": "autofresh",
        "command": "Kraken gain filleul 20 €",
        "requester": {"source": "hermes", "identity": "...", "token": "..."},
        "options": {"persist": true, "plan": true, "run_writers": true},
        "correlation_id": "optional"
      }
    """
    action = str(body.get("action") or "autofresh").strip().lower()
    if action not in {"autofresh", "operator", "operator_sync"}:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "errors": [{"code": "unknown_action", "detail": action}],
            "at": _now(),
        }
    opts = body.get("options") or {}
    result = run_autofresh_command(
        str(body.get("command") or ""),
        requester=body.get("requester"),
        persist=bool(opts.get("persist", True)),
        plan=bool(opts.get("plan", True)),
        run_writers=bool(opts.get("run_writers", True)),
        correlation_id=body.get("correlation_id"),
    )
    save_result(result)
    return result
