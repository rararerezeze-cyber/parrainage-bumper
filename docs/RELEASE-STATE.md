# AutoFresh — release state

`AUTOFRESH_RELEASE_STATUS = FINISHED_WITH_KNOWN_LIMITATIONS`

AutoFresh is in production. The engineering work needed for the current supported
scope is complete; remaining gaps are external platform constraints, not open
automation work. Runtime safety gates remain active and are not relaxed by this
release status.

Authority order:

1. `data/platform-write-status.json` — platform capability and proof
2. `data/autofresh-phase.json` — runtime phase and authorization flags
3. `data/pending_writes.json` — unresolved write lifecycle
4. current scheduler/ledger state
5. this document and the rest of `docs/`
6. older historical captures

See `docs/CLOSURE-2026-09-11.md` for the final production closure.

## Current production architecture

- Slack is the primary operator interface.
- The Cloudflare Worker `autofresh-slack` verifies Slack requests and dispatches
  the GitHub backend. No local process is required.
- Cloudflare Cron wakes `bump_autres_scheduler.yml` at minutes 03/18/33/48.
  There is no duplicate native GitHub cron for this scheduler.
- The scheduler persists five random UTC slots per day. Only a due persisted slot
  can dispatch the real Code-Parrainage / Parrainage.co browser cycle.
- Per-site bump outcomes are durable: a site already confirmed successful is not
  replayed, and only a failure proven to occur before any site action is eligible
  for one bounded recovery.
- Super-Parrain remains on its independent ~24 h minimum + persistent random delay.
- The monitor stays observation-only; `monitor_auto_accept=false`.

## Platform table

| Plateforme | État | Autonomie actuelle | Bump | Mise à jour contenu |
|---|---|---|---|---|
| Super-Parrain | `WRITE_VERIFIED` | `FUSED_UPDATE_BUMP` | automatique | automatique sur vraie différence sûre |
| Parrainage.co | `WRITE_VERIFIED` | `PC_OFF_READY` | 5 slots aléatoires/jour | automatique sur vraie différence sûre |
| Code-Parrainage | `WRITE_VERIFIED` | `PC_OFF_READY` | 5 slots aléatoires/jour | automatique sur vraie différence sûre |
| 1Parrainage | `WRITE_VERIFIED` | `PC_OFF_READY` | — | automatique sur vraie différence sûre |
| ReferralCodes | `CANARY_READY` | `NEVER_AUTO_COMMIT` | — | jamais automatique |
| ReferralCode.tv | `WRITE_VERIFIED` | `HUMAN_SAVE_REQUIRED` | manuel uniquement | humain, CAPTCHA requis |
| ReferralDrop | `AUTH_BLOCKED_MANUAL` | manuel | — | humain |

`WRITE_VERIFIED = 5/7` does not mean five unattended writers. The durable
unattended content-writer allow-list remains Parrainage.co, Code-Parrainage and
1Parrainage; Super-Parrain uses its separate fused production route.

## Bumper state

### Code-Parrainage + Parrainage.co

The five real cycles are driven only by
`data/bump-autres-schedule.json`. Cloudflare is a wake-up layer, not a second
schedule. The native GitHub poller was removed after Cloudflare was proven live.

On 2026-09-11:
- early slots exposed the old partial-accounting defect: Parrainage.co succeeded
  while Code-Parrainage failed;
- the ledger was upgraded to store per-site outcomes and safe retryability;
- run `34607923542` completed both sites successfully;
- run `34629797313` completed both sites successfully;
- Code-Parrainage's changed slider-success signal is therefore proven in live
  production, not only by tests.

### ReferralCode.tv

There is no scheduled retry loop anymore. GitHub-hosted Chromium is blocked
before login by a standalone Cloudflare Turnstile challenge. Repeating the same
known blocker several times per day had no production value, so
`bump_referralcode_tv.yml` is manual-only. It remains available for an explicit
future re-test if the external gate changes. No challenge bypass is implemented.

## Operator path

Production control is:

```
Slack /autofresh
  → Cloudflare Worker
  → GitHub Actions (hermes_operator.yml; historical filename)
  → AutoFresh backend
  → Slack reply
```

Every slash command starts unarmed. A real writer can only be armed by the
separate signed, allowlisted **Confirmer l'écriture** interaction, and the backend
still applies platform readiness, SAFE_DIFF, backup, post-write reread,
post-verification and circuit-breaker rules.

A future naturally occurring real SAFE_DIFF will provide one more end-to-end
operational sample through the final Slack UX. It is deliberately not manufactured
and is not an open engineering blocker.

## Workflows

- `PRODUCTION_SCHEDULED`: Super-Parrain and the daily observation monitor.
- `PRODUCTION_EXTERNAL_TRIGGER`: randomized Code-Parrainage / Parrainage.co
  scheduler, woken by Cloudflare.
- `PRODUCTION_MANUAL`: real browser cycle, Slack backend, explicit controlled
  write routes, ReferralCode.tv manual re-test.
- closed canaries/diagnostics remain historical evidence and stay runtime-gated.
- legacy Telegram workflows remain optional/test-only.

## Known external limitations

These do not block the finished supported scope:

1. ReferralCode.tv content save requires a human CAPTCHA, and GitHub-hosted login
   is currently stopped by standalone Turnstile.
2. ReferralCodes has no proven documented update path suitable for safe unattended
   mutation; `NEVER_AUTO_COMMIT` remains mandatory.
3. ReferralDrop has no supported unattended authentication/write route.
4. Some public offer-monitor sources return anti-bot responses. They remain
   classified as external blockers; no bypass is attempted.
5. 1Parrainage can enforce a daily edit quota.
6. Super-Parrain's already-authenticated-session retry branch has not needed a
   natural live exercise; the normal production path is proven.

## Safety invariants that must not be weakened

- no synthetic write merely to manufacture proof;
- no CAPTCHA/Turnstile bypass;
- no speculative ReferralCodes Commit;
- no replay of a site after an ambiguous post-action failure;
- monitor remains observation-only unless explicitly redesigned;
- real writes still require explicit confirmation where the operator path calls
  for it and full post-write verification.
