"""Stable Hermes → Autofresh operator interface.

Architecture:
  Telegram → Hermes (owns bot) → Autofresh (this module / GH Actions) → JSON → Hermes → Telegram

Autofresh does NOT own a Telegram bot. Auth is Hermes/GitHub shared token, not BotFather.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.offers import OffersRepository
from lib.operator_overrides import OperatorOverrideStore
from lib.operator_plan import format_plan_report, plan_program_impact
from lib.paths import DATA_DIR, OPERATOR_OVERRIDES_PATH
from lib.write_status import (
    ALL_PLATFORMS,
    ROUTE_AUTO_ON_SAFE_DIFF,
    ROUTE_HUMAN_SAVE_REQUIRED,
    human_local_command,
    runtime_route,
    summary as write_summary,
)

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


class _FileLock:
    """Cross-platform exclusive lock — one Hermes mutating command at a time."""

    def __init__(self, path: Path, timeout: float = 30.0):
        self.path = path
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.time() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    self._fh.write(" ")
                    self._fh.flush()
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"hermes_lock_timeout:{self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fh:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


def _lock_path() -> Path:
    return Path(OPERATOR_OVERRIDES_PATH).parent / ".hermes.lock"


def _idempotency_path() -> Path:
    return Path(RESULT_PATH).parent / "hermes-idempotency.json"


def _load_idempotency() -> dict[str, Any]:
    p = _idempotency_path()
    if not p.exists():
        return {"keys": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"keys": {}}


def _save_idempotency(data: dict[str, Any]) -> None:
    p = _idempotency_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _confirm_override_persisted(parsed: dict | None, result: dict | None) -> bool:
    """Re-read the overrides file and confirm the applied mutation is on disk."""
    if not parsed or parsed.get("action") not in {"set", "remove"}:
        return True
    store = OperatorOverrideStore()
    items = store.load()
    program = parsed.get("program")
    field = parsed.get("field")
    platform = parsed.get("platform")
    if parsed.get("action") == "set":
        expected = (result or {}).get("new_effective")
        for o in items:
            if o.program == program and o.field == field and o.platform == platform:
                return expected is None or str(o.value) == str(expected)
        return False
    # remove: matching override must be gone
    for o in items:
        if o.program == program and o.field == field and o.platform == platform:
            return False
    return True


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
    base["serialized"] = True
    base["persist_confirmed"] = False

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
        base["routing"] = routing_summary(base["platforms"])
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
        base["persist_confirmed"] = True  # nothing to persist
        return base

    replay_key = _idempotency_key(command, parsed, None)
    lock_cm = _FileLock(_lock_path()) if persist and parsed.get("action") in {"set", "remove"} else None
    if lock_cm:
        try:
            lock_cm.__enter__()
        except TimeoutError as exc:
            base["errors"].append({"code": "serialized_lock_timeout", "detail": str(exc)})
            return base
    try:
        return _run_autofresh_command_locked(
            base,
            command,
            parsed,
            persist=persist,
            plan=plan,
            run_writers=run_writers,
            replay_key=replay_key,
        )
    finally:
        if lock_cm:
            lock_cm.__exit__(None, None, None)


def _format_divergences_fr(program: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return f"Aucune divergence en attente pour {program}."
    lines = [f"⚠️ {len(items)} divergence(s) en attente pour {program} :"]
    for it in items:
        lines.append(
            f"• {it.get('platform')} / {it.get('field')} : "
            f"{it.get('curated_value')!r} → {it.get('observed_value')!r}"
        )
    return "\n".join(lines)


# Pure read-only meta-actions: never persist, never lock, never invoke a
# writer, never touch format_plan_report (which only knows about
# status/list/set/remove). Short-circuited here, before any of that
# machinery, rather than teaching format_plan_report a 4th/5th/6th action.
_READ_ONLY_META_ACTIONS = {"help", "divergences", "plateformes_program"}


def _run_autofresh_command_locked(
    base: dict[str, Any],
    command: str,
    parsed: dict,
    *,
    persist: bool,
    plan: bool,
    run_writers: bool,
    replay_key: str,
) -> dict[str, Any]:
    action = parsed.get("action")
    if action in _READ_ONLY_META_ACTIONS:
        result = apply_operator_command(parsed, message=command)
        base["result"] = result
        base["ok"] = True
        base["persist_confirmed"] = True
        if action == "divergences":
            base["human_summary"] = _format_divergences_fr(
                parsed.get("program") or "", result.get("divergences") or []
            )
        else:
            base["human_summary"] = result.get("text", "")
        base["platforms"] = []
        base["write_status"] = write_summary()
        base["idempotency_key"] = replay_key
        return base

    # A "set" re-dispatched with run_writers=true must always actually
    # attempt the writer, even when the override value itself was already
    # persisted by an earlier (writer-less) dispatch of the identical
    # command — the two-phase Slack "preview -> confirm write" flow relies
    # on exactly this: same command, same idempotency key, second call adds
    # run_writers=true. Without this guard the idempotent-replay short
    # circuit below would return the first call's cached result and the
    # confirm button would silently never run a writer. "remove" and any
    # writer-less "set" replay keep the original fast-path unchanged.
    skip_idempotent_replay = run_writers and parsed.get("action") == "set"
    if persist and parsed.get("action") in {"set", "remove"} and not skip_idempotent_replay:
        ledger = _load_idempotency()
        prev = (ledger.get("keys") or {}).get(replay_key)
        if prev and prev.get("ok") and prev.get("persist_confirmed"):
            base["ok"] = True
            base["idempotent"] = True
            base["replayed"] = True
            base["persist_confirmed"] = True
            base["result"] = prev.get("result") or {"note": "idempotent_replay"}
            base["idempotency_key"] = replay_key
            base["human_summary"] = prev.get("human_summary") or "idempotent replay"
            base["write_status"] = write_summary()
            base["platforms"] = []
            return base

    try:
        if persist and parsed.get("action") in {"set", "remove"}:
            try:
                from lib.safety import snapshot_state

                snapshot_state(f"hermes:{parsed.get('action')}:{parsed.get('program')}")
            except Exception:
                pass
        result = apply_operator_command(parsed, message=command)
    except ValueError as exc:
        base["errors"].append({"code": "apply_error", "detail": str(exc)})
        return base
    except KeyError as exc:
        base["errors"].append({"code": "unknown_program", "detail": str(exc)})
        return base

    base["result"] = result
    if persist:
        try:
            confirmed = _confirm_override_persisted(parsed, result)
        except Exception as exc:  # noqa: BLE001
            confirmed = False
            base["errors"].append({"code": "persist_verify_error", "detail": str(exc)})
        base["persist_confirmed"] = confirmed
        if parsed.get("action") in {"set", "remove"} and not confirmed:
            base["errors"].append(
                {
                    "code": "persist_unconfirmed",
                    "detail": "override file re-read did not match applied mutation",
                }
            )
            base["ok"] = False
            return base
    else:
        base["persist_confirmed"] = True

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
    base["routing"] = routing_summary(base["platforms"])
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
            # An identical/restored override is not proof of a content diff.
            # Only the matching native plan may enqueue deferred work.
            has_super_diff = any(
                row.get("platform") == "super-parrain"
                and row.get("status") == "pending_update"
                and bool(row.get("changed_fields"))
                for row in base["platforms"]
            )
            if parsed.get("platform") in (None, "super-parrain") and has_super_diff:
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
    human_lines: list[str] = []
    for row in base.get("platforms") or []:
        if row.get("route") != ROUTE_HUMAN_SAVE_REQUIRED:
            continue
        if not (row.get("changed_fields") or row.get("status") == "pending_update"):
            continue
        cmd = row.get("human_command") or human_local_command(str(row.get("platform") or ""))
        human_lines.append(
            f"HUMAN_SAVE_REQUIRED {row.get('platform')}: do not auto-save. {cmd}"
        )
    if human_lines:
        human += "\n\n" + "\n".join(human_lines)
    base["human_summary"] = human
    base["idempotency_key"] = _idempotency_key(command, parsed, result)
    base["ok"] = len(base["errors"]) == 0 and bool(base.get("persist_confirmed", True))
    base["artifacts"] = {
        "overrides_path": str(OPERATOR_OVERRIDES_PATH),
        "result_path": str(RESULT_PATH),
    }
    if persist and base["ok"] and parsed.get("action") in {"set", "remove"}:
        ledger = _load_idempotency()
        keys = ledger.setdefault("keys", {})
        keys[replay_key] = {
            "ok": True,
            "persist_confirmed": True,
            "at": _now(),
            "result": {
                "action": result.get("action") if isinstance(result, dict) else None,
                "new_effective": result.get("new_effective") if isinstance(result, dict) else None,
                "new_source": result.get("new_source") if isinstance(result, dict) else None,
            },
            "human_summary": base.get("human_summary"),
        }
        # keep ledger bounded
        if len(keys) > 200:
            for old in list(keys.keys())[: len(keys) - 200]:
                keys.pop(old, None)
        _save_idempotency(ledger)
    return base


def _platform_rows(plan_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in plan_data.get("platforms") or []:
        plat = p.get("platform")
        route = p.get("route") or runtime_route(str(plat or ""))
        pending = bool(p.get("changed_fields")) and p.get("status") == "pending_update"
        rows.append(
            {
                "platform": plat,
                "status": p.get("status"),
                "write_mode": p.get("write_mode"),
                "route": route,
                "can_auto_write": bool(
                    p.get("can_auto_write")
                    and route == ROUTE_AUTO_ON_SAFE_DIFF
                    and pending
                ),
                "changed_fields": p.get("changed_fields") or {},
                "error": p.get("error"),
                "human_command": p.get("human_command") or human_local_command(str(plat or "")),
            }
        )
    if not rows:
        for p in write_summary().get("platforms") or []:
            plat = p.get("platform")
            route = p.get("route") or runtime_route(str(plat or ""))
            rows.append(
                {
                    "platform": plat,
                    "status": p.get("status"),
                    "write_mode": p.get("status"),
                    "route": route,
                    "can_auto_write": False,
                    "changed_fields": {},
                    "telegram_action": p.get("telegram_action"),
                    "human_command": human_local_command(str(plat or "")),
                }
            )
    return rows


def routing_summary(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Machine-readable Monitor→Hermes dispatch classes. No live write."""
    automatic: list[str] = []
    human: list[dict[str, str]] = []
    blocked: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        plat = str(row.get("platform") or "")
        if not plat:
            continue
        seen.add(plat)
        route = str(row.get("route") or runtime_route(plat))
        if route == ROUTE_AUTO_ON_SAFE_DIFF:
            automatic.append(plat)
        elif route == ROUTE_HUMAN_SAVE_REQUIRED:
            human.append(
                {
                    "platform": plat,
                    "route": route,
                    "command": str(row.get("human_command") or human_local_command(plat) or ""),
                }
            )
        else:
            blocked.append(plat)
    for plat in ALL_PLATFORMS:
        if plat in seen:
            continue
        route = runtime_route(plat)
        if route == ROUTE_AUTO_ON_SAFE_DIFF:
            automatic.append(plat)
        elif route == ROUTE_HUMAN_SAVE_REQUIRED:
            human.append(
                {
                    "platform": plat,
                    "route": route,
                    "command": human_local_command(plat) or "",
                }
            )
        else:
            blocked.append(plat)
    return {
        "automatic_safe_diff_targets": automatic,
        "human_routed_targets": human,
        "blocked_targets": blocked,
        "monitor": "OBSERVATION_ONLY",
    }


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
