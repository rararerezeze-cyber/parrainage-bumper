# AutoFresh handoff — current production state

Repository: `rararerezeze-cyber/parrainage-bumper`, branch `main`.

AutoFresh is production-ready for its supported scope. Start from current state
files, not old captures:

1. `data/platform-write-status.json`
2. `data/autofresh-phase.json`
3. `data/pending_writes.json`
4. `data/bump-autres-schedule.json`
5. `data/bump-autres-dispatch-ledger.json`
6. `docs/RELEASE-STATE.md`

Release status: `FINISHED_WITH_KNOWN_LIMITATIONS`.

## Operator/runtime ownership

Slack is the primary operator interface. The production path is:

```
Slack → Cloudflare Worker autofresh-slack
      → GitHub Actions hermes_operator.yml
      → backend / writers
      → Slack
```

The `hermes_operator.yml` filename is historical. Hermes/Telegram is not required
for normal production and the legacy Telegram workflows must not be enabled as a
second production control plane without a fresh review.

Production workflows use `AUTOFRESH_PHASE=PRODUCTION`.

## Platform state

| Platform | State | Operational rule |
|---|---|---|
| Super-Parrain | `WRITE_VERIFIED` | `FUSED_UPDATE_BUMP`; ~24 h minimum + persistent random delay |
| Parrainage.co | `WRITE_VERIFIED` | PC-off writer; combined randomized bumper |
| Code-Parrainage | `WRITE_VERIFIED` | PC-off writer; combined randomized bumper; slider production fix proven |
| 1Parrainage | `WRITE_VERIFIED` | PC-off writer; daily edit quota may block safely |
| ReferralCodes | `CANARY_READY` | `NEVER_AUTO_COMMIT` |
| ReferralCode.tv | `WRITE_VERIFIED` | content save human-only; bump workflow manual-only while GitHub login is Turnstile-blocked |
| ReferralDrop | `AUTH_BLOCKED_MANUAL` | manual only |

Do not infer unattended capability from the 5/7 WRITE_VERIFIED count. Follow the
platform autonomy field and write-status gates.

## Code-Parrainage / Parrainage.co scheduling

Cloudflare Cron is the sole routine wake-up for
`.github/workflows/bump_autres_scheduler.yml` at minutes 03/18/33/48. The
workflow itself has no native GitHub `schedule` trigger.

The scheduler persists exactly five random UTC slots/day. Polling never becomes a
real site visit unless a persisted slot is due.

The real browser workflow records outcomes per site. Important invariant:

- successful site → durable completion, never replayed for that slot;
- failure before any site action → may be marked retryable;
- ambiguous/post-action failure → never automatically replayed;
- at most one delayed safe recovery.

Live 2026-09-11 proof:
- run `34607923542`: Code-Parrainage + Parrainage.co success;
- run `34629797313`: Code-Parrainage + Parrainage.co success.

Earlier partial runs on the same day are retained truthfully as Parrainage.co-only
successes rather than falsely counted as complete cycles.

## ReferralCode.tv

`.github/workflows/bump_referralcode_tv.yml` is manual-only. GitHub-hosted
Chromium receives a standalone Cloudflare Turnstile page before the login form.
Repeated scheduled attempts were removed because they deterministically repeated
the same external blocker.

Keep:
- no bypass;
- no automatic scheduled retry;
- explicit manual `workflow_dispatch` only if checking whether the external gate
  has changed;
- human save for content because the site requires CAPTCHA.

## Monitor

`monitor_offers.yml` runs daily and remains `OBSERVATION_ONLY`.
`monitor_auto_accept=false`. Anti-bot responses are reported/classified, not
worked around.

## Slack safety

- signed Slack request required;
- allowlisted user required;
- slash command uses `run_writers=false`;
- signed confirmation button is the only Slack path that can request
  `run_writers=true`;
- backend readiness/SAFE_DIFF/circuit breakers remain authoritative;
- backup + reread + post-verification remain mandatory;
- failed workflow must never be rendered as success.

A future natural SAFE_DIFF is useful additional production evidence but must not
be fabricated just to make a test green.

## Do not reopen closed work without new evidence

- do not retry ReferralCodes Commit experimentally;
- do not add CAPTCHA/Turnstile solving;
- do not restore ReferralCode.tv cron while the blocker is unchanged;
- do not restore the duplicate GitHub scheduler cron;
- do not turn monitor auto-accept on casually;
- do not replay a bump after an ambiguous site action;
- do not weaken per-site ledger semantics.

Historical closure/audit documents remain useful evidence, but
`docs/CLOSURE-2026-09-11.md` and `docs/RELEASE-STATE.md` describe the current
production state.
