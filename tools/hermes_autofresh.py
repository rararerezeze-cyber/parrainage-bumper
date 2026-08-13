#!/usr/bin/env python3
"""Hermes → Autofresh stable CLI (machine-readable JSON).

Usage:
  # Local/dev (requires AUTOFRESH_ALLOW_LOCAL_OPERATOR=1 or operator token)
  python tools/hermes_autofresh.py --command "Kraken status"

  # JSON request on stdin
  echo '{"action":"autofresh","command":"Kraken status","requester":{"source":"hermes"}}' \\
    | python tools/hermes_autofresh.py --stdin

  # GitHub Actions / Hermes dispatch body file
  python tools/hermes_autofresh.py --request-file /tmp/req.json

Output: single JSON object on stdout (also saved to data/captures/hermes-last-result.json).
Exit codes: 0 ok, 2 parse/auth/business error, 1 unexpected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.hermes_interface import handle_request, run_autofresh_command, save_result


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes Autofresh operator interface")
    ap.add_argument("--command", "-c", default="", help="Natural language operator command")
    ap.add_argument("--stdin", action="store_true", help="Read full JSON request from stdin")
    ap.add_argument("--request-file", default="", help="Path to JSON request file")
    ap.add_argument("--requester-source", default="hermes")
    ap.add_argument("--requester-identity", default="")
    ap.add_argument("--token", default="", help="Operator token (or env AUTOFRESH_OPERATOR_TOKEN)")
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--no-plan", action="store_true")
    ap.add_argument("--no-writers", action="store_true")
    ap.add_argument("--correlation-id", default="")
    ap.add_argument(
        "--allow-local",
        action="store_true",
        help="Set AUTOFRESH_ALLOW_LOCAL_OPERATOR=1 for this run",
    )
    args = ap.parse_args()

    if args.allow_local:
        os.environ["AUTOFRESH_ALLOW_LOCAL_OPERATOR"] = "1"

    if args.stdin:
        try:
            body = json.load(sys.stdin)
        except Exception as exc:
            err = {
                "ok": False,
                "errors": [{"code": "invalid_json", "detail": str(exc)}],
            }
            print(json.dumps(err, ensure_ascii=False))
            return 2
        result = handle_request(body)
    elif args.request_file:
        body = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        result = handle_request(body)
    else:
        if not args.command.strip():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "errors": [
                            {
                                "code": "empty_command",
                                "detail": "provide --command or --stdin/--request-file",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        token = args.token or os.environ.get("AUTOFRESH_OPERATOR_TOKEN") or os.environ.get(
            "HERMES_SHARED_TOKEN", ""
        )
        result = run_autofresh_command(
            args.command,
            requester={
                "source": args.requester_source,
                "identity": args.requester_identity or args.requester_source,
                "token": token,
            },
            persist=not args.no_persist,
            plan=not args.no_plan,
            run_writers=not args.no_writers,
            correlation_id=args.correlation_id or None,
        )
        save_result(result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
