# Hermes → Autofresh interface

Autofresh is a **backend** for Hermes. Hermes owns the Telegram bot.

```
Telegram user
  → Hermes (allowlist + intent)
  → Autofresh operator interface
  → GitHub Actions / CLI
  → structured JSON result
  → Hermes formats Telegram reply
```

Autofresh does **not** require BotFather, webhook, or a dedicated Telegram bot.

## Request

```json
{
  "action": "autofresh",
  "command": "Kraken gain filleul 20 €",
  "requester": {
    "source": "hermes",
    "identity": "hermes-main",
    "token": "<AUTOFRESH_OPERATOR_TOKEN if configured>"
  },
  "options": {
    "persist": true,
    "plan": true,
    "run_writers": true
  },
  "correlation_id": "optional-uuid"
}
```

## CLI

```bash
# Local test
export AUTOFRESH_ALLOW_LOCAL_OPERATOR=1
python tools/hermes_autofresh.py --command "Kraken status" --allow-local

# With shared token
export AUTOFRESH_OPERATOR_TOKEN=...
python tools/hermes_autofresh.py --command "Kraken gain filleul 20 €" --token "$AUTOFRESH_OPERATOR_TOKEN"

# Stdin JSON
echo '{"action":"autofresh","command":"Kraken status","requester":{"source":"hermes"}}' \
  | AUTOFRESH_ALLOW_LOCAL_OPERATOR=1 python tools/hermes_autofresh.py --stdin
```

## GitHub Actions (production path)

Workflow: `hermes_operator.yml`

Hermes dispatches:

```http
POST /repos/{owner}/{repo}/actions/workflows/hermes_operator.yml/dispatches
Authorization: Bearer <fine-grained PAT with actions:write>
{
  "ref": "main",
  "inputs": {
    "command": "Kraken gain filleul 20 €",
    "requester": "hermes-main",
    "correlation_id": "…",
    "run_writers": "true"
  }
}
```

`ref` must always be `main`. An old feature branch (e.g.
`autofresh/phase2b-kraken-capture`) may still exist on the remote long after
merge — dispatching against it silently runs a stale, pre-hardening copy of
this workflow instead of the current one on `main`.

Then Hermes downloads artifact `hermes-autofresh-result` → `hermes-last-result.json`.

For mutating commands, the workflow commit is part of success. Durable snapshots
and circuit-breaker state are staged with the override/result; the runner-only
`data/audit/events.jsonl` line is stashed before the single rebase/push attempt.
Any other residual tracked or untracked path fails closed. A JSON artifact may
therefore describe the runner's attempted mutation while a red workflow means
that mutation was not durably persisted; Hermes must report the persistence
failure rather than claiming success.

Legacy workflow `telegram_sync.yml` remains for compatibility (still message-driven). Prefer `hermes_operator.yml` for Hermes.

## Response (always JSON)

```json
{
  "schema_version": 1,
  "ok": true,
  "action": "autofresh",
  "command": "…",
  "parsed": { "action": "set", "program": "kraken", "field": "referee_reward", "value": "20 €", "platform": null },
  "result": { "old_effective": "…", "new_effective": "20 €", "new_source": "GLOBAL_OPERATOR_OVERRIDE" },
  "platforms": [
    {
      "platform": "super-parrain",
      "status": "pending_update",
      "write_mode": "CANARY_READY",
      "can_auto_write": false,
      "changed_fields": { "referee_reward": { "old": "…", "new": "20 €" } }
    }
  ],
  "write_status": {
    "WRITE_VERIFIED": "0/7",
    "telegram_live_capable": []
  },
  "writers": { "reports": [] },
  "human_summary": "text Hermes may use or rewrite",
  "idempotency_key": "…",
  "monitor": "OBSERVATION_ONLY",
  "errors": []
}
```

### French UX meta-commands

`parsed.action` can also be `"help"`, `"divergences"`, or `"plateformes_program"`
(see AGENTS.md's "Global French UX meta-commands" section for the exact trigger
phrases). All three are pure reads — `writers` is always `null`, `platforms` is
always `[]`, and `human_summary` is the ready-to-send French text (menu, example
list, per-platform table, or a divergence list) — relay it as-is.

### Platform rows for Hermes UI

| `write_mode` / status | Hermes should say |
|----------------------|-------------------|
| `WRITE_VERIFIED` + `can_auto_write` | UPDATED / LIVE |
| `CANARY_READY` | PLAN_ONLY (canary not auto from Telegram bulk) |
| `WRITE_PREPARED` | PLAN_ONLY |
| `AUTH_BLOCKED_GOOGLE` | AUTH_BLOCKED |

## Security

1. **Hermes** enforces Telegram allowlist (user-facing).
2. **Autofresh** accepts only:
   - authenticated GitHub workflow_dispatch, and/or
   - `AUTOFRESH_OPERATOR_TOKEN` / `HERMES_SHARED_TOKEN` on the request.
3. No public unauthenticated HTTP endpoint is required for the product.

## Optional telegram-worker

`telegram-worker/` is **optional/test only**. It is not required when Hermes owns Telegram.
Do not deploy it as the production bot for Autofresh.

## Gate

`HERMES_AUTOFRESH_INTERFACE_READY = YES` when:

- text command in → structured JSON out
- global + platform overrides
- PLAN_ONLY vs WRITE_VERIFIED in platform rows
- structured errors
- idempotence notes
- no BotFather/webhook dependency for product path

`HERMES_PRODUCTION_READY = YES` additionally requires:

- process-level file lock (one mutating command at a time)
- override file re-read (`persist_confirmed`) before `ok=true` on set/remove
- idempotency ledger replay (same command does not re-apply)
- snapshot of overrides/status before mutation
- no secrets in JSON / logs (token names only)
