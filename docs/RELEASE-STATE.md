# AutoFresh — release state

`AUTOFRESH_RELEASE_STATUS = READY_WITH_KNOWN_LIMITATIONS`

Authority order — this document never overrides the state files:

1. `data/platform-write-status.json` — platform capability and proof
2. `data/autofresh-phase.json` — runtime phase and authorization flags
3. `data/pending_writes.json` — unresolved write lifecycle
4. the most recent capture named by the status record
5. this document and the rest of `docs/`
6. older historical artifacts

## Platform table

| Plateforme | Status | Autonomie | Bump | Content update | PC off | Limitation |
|---|---|---|---|---|---|---|
| Super-Parrain | `WRITE_VERIFIED` | `FUSED_UPDATE_BUMP` | auto, ~24 h + jitter | auto on real SAFE_DIFF (fused with the bump) | oui | the already-authenticated-session retry branch has never been exercised live |
| Parrainage.co | `WRITE_VERIFIED` | `PC_OFF_READY` | auto (combined cycle) | auto on real SAFE_DIFF | oui | — |
| Code-Parrainage | `WRITE_VERIFIED` | `PC_OFF_READY` | auto (combined cycle) | auto on real SAFE_DIFF | oui | — |
| 1Parrainage | `WRITE_VERIFIED` | `PC_OFF_READY` | — | auto on real SAFE_DIFF | oui — `gh_headless_save=PROVEN` | daily edit quota can block a save; it is reported, never worked around |
| ReferralCodes | `CANARY_READY` | `NEVER_AUTO_COMMIT` | — | jamais automatique | non | Agent Import is not a documented update path for an existing referral |
| ReferralCode.tv | `WRITE_VERIFIED` | `HUMAN_SAVE_REQUIRED` | `BUMP_ISOLATED_BEST_EFFORT` | humain (local headed) | non | save requires a CAPTCHA; GitHub login may hit a Cloudflare Turnstile gate |
| ReferralDrop | `AUTH_BLOCKED_MANUAL` | `AUTH_BLOCKED_MANUAL` | — | humain | non | no official public write path |

`WRITE_VERIFIED = 5/7`. That count is **not** a licence to write: a platform is
eligible for an unattended write only when it is `WRITE_VERIFIED` **and**
`PC_OFF_READY`, which is exactly what `telegram_live_capable` lists.

- `PC_OFF_READY`: Parrainage.co, Code-Parrainage, 1Parrainage
- `BUMP_AUTONOMOUS`: Super-Parrain, Parrainage.co, Code-Parrainage, and
  ReferralCode.tv on a best-effort basis
- `CONTENT_UPDATE_AUTONOMOUS`: Super-Parrain (fused), Parrainage.co,
  Code-Parrainage, 1Parrainage — on a real SAFE_DIFF only
- `HUMAN_REQUIRED`: ReferralCode.tv (content save), ReferralCodes, ReferralDrop

## ReferralCode.tv — isolated bump

`.github/workflows/bump_referralcode_tv.yml` runs ReferralCode.tv **alone**: its
own concurrency group, its own `TARGET_SITES`, only the ReferralCode.tv
credentials, no CAPTCHA-solver key, `contents: read`, and a cron offset from
`bump_autres.yml` so the two never start together. It cannot delay, cancel, or
fail the healthy Code-Parrainage / Parrainage.co cycle.

Per cycle:

1. normal login attempt;
2. if the account is reachable → `/my-account/?tab=listings`, and at most **one**
   `#cliccami` boost when quota allows;
3. if the standalone Cloudflare Turnstile interstitial is served → stop
   immediately. No solve, no click, no retry, no credential submitted into the
   challenge page. The cycle is classified `EXPECTED_EXTERNAL_BLOCKER` /
   `AUTH_BLOCKED_CHALLENGE`, reported once per day through the notification
   contract, and the run stays green.

A permanently red workflow on a known, documented, unfixable-from-GitHub gate is
noise that would hide a real regression. Real failures — network, DOM drift on an
authenticated page, bad credentials — still fail the run.

No legitimate unattended GitHub login path exists today: the challenge appears
before the login form, so there is no session to obtain without solving it. This
is a `KNOWN_LIMITATION`, not an open engineering task.

## Observability

`lib/notify.py` is the single event contract. AutoFresh never talks to Telegram:

```
AutoFresh runtime → lib.notify.emit() → data/notifications/outbox.jsonl
                  → Hermes (local plugin) → Telegram
```

- Levels: `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `HUMAN_REQUIRED`.
- Fields: `level, platform, program, event, field, old_value, new_value, source,
  action, result, post_match, exact, immutable, pc_required, block_reason,
  timestamp, run_id`.
- Reported: real write, post-verify success/failure, monitor SAFE_DIFF, platform
  status change, workflow error, human required, rollback, pending
  created/closed, circuit breaker open, real canary, notable bump, external
  blocker.
- Never reported: routine `NO_CHANGE` cycles, polls, technical chatter. This is
  an allow-list, so an unknown event is dropped rather than guessed.
- Deduplicated per event with a TTL (24 h for external blockers and human-required,
  so a 5-hourly cron reports once, not five times a day). Because
  `data/notifications/` is gitignored, every runner starts empty — the TTL is only
  durable because each production workflow restores and saves
  `data/notifications/dedup.json` through a rolling `actions/cache` key.
  **Scope: per workflow, not global.** Two workflows keep independent dedup state,
  and two concurrent runs of the same workflow can both restore the same entry and
  both emit. The failure mode is therefore one duplicate notification, never a
  suppressed one — fail-safe by design. Both cache steps are
  `continue-on-error`, so a cache miss or outage cannot fail the business job.
- `BEST_EFFORT` / `FAIL_OPEN`: `emit()` never raises. A dead notification path can
  never fail a bump or a business write.
- No secrets: closed field whitelist, credential-shaped values redacted, values
  length-capped.

Read it with `python tools/notify_digest.py --since-hours 24` or
`--daily-summary`. The outbox is gitignored (runner-only, no commit noise) and
uploaded as a workflow artifact by **all seven** production workflows — an event
emitted in a runner and never uploaded would simply be lost.

**What remains to connect, outside this repository:** the Hermes plugin must
fetch the `autofresh-notifications-*` artifact (or run `tools/notify_digest.py`
where it has a checkout) and relay the records to Telegram. That plugin is not in
this repository and was not modified from here.

## Workflows

`data/workflow-registry.json` classifies all 24 workflows and
`tests/test_workflow_registry.py` fails if one is added, removed, or
reclassified.

- `PRODUCTION_SCHEDULED`: `bump_super_parrain.yml`, `bump_autres.yml`,
  `bump_referralcode_tv.yml`, `monitor_offers.yml`
- `PRODUCTION_MANUAL`: `hermes_operator.yml`, `controlled_write.yml`,
  `activation_canary.yml`
- `CI_READ_ONLY`: `ci.yml`
- `EVIDENCE_CLOSED`: `canary_write_1parrainage.yml`
- `CANARY_CLOSED`: `canary_write_code_parrainage.yml`,
  `canary_write_parrainage_co.yml`
- `DIAGNOSTIC_CLOSED`: the four `diagnose_*`, the five `inspect_*`,
  `preflight_1parrainage_headless.yml`, `probe_super_mes_annonces.yml`
- `DIAGNOSTIC_AVAILABLE`: `capture_readonly.yml`
- `OPTIONAL_TEST_ONLY`: `deploy_telegram_worker.yml`, `telegram_sync.yml`

The closed ones are kept as historical proof and are already refused at runtime,
not merely by convention:

- `may_execute_canary()` returns `already_WRITE_VERIFIED` for Parrainage.co,
  Code-Parrainage, 1Parrainage and ReferralCode.tv;
- `guard_live_evidence_probe()` requires `gh_headless_save == NOT_RUN`, and it is
  `PROVEN`, so the 1Parrainage proof cannot be re-run (demonstrated by run
  `32559814742`: refused before browser startup, 0 Save clicks, 0 platform
  writes);
- `platforms.referralcodes.writer.execute_write(dry_run=False)` returns
  `NEVER_AUTO_COMMIT` before reaching any Commit code;
- `super_parrain_canary_allowed()` refuses a Super-Parrain canary outright while
  the platform is `WRITE_VERIFIED` + `NORMAL_BUMP`.

### Super-Parrain: one owner for the production cycle

`activation_canary.yml` lost its two daily schedules. They existed only to make it
the sole live saver *while* Super-Parrain was `CANARY_PENDING`; that condition
ended when the platform reached `WRITE_VERIFIED` with the historical bumper
authorized. `FUSED_UPDATE_BUMP` in `bump_super_parrain.yml` is now the single
owner of the production cycle — it already updates on a real SAFE_DIFF and bumps
otherwise. The workflow is kept (manual) for the other platforms and status-only,
and passes `--canary`, which the runtime guard refuses for Super-Parrain.

`tools/controlled_write_super_parrain.py` also no longer queues a pending on
`--execute` before building the plan. A pending is opened only where a real
content diff exists: on a cooldown abort (so the fused cycle prefers update at the
next slot) and immediately before a genuine eligible write, which
`mark_pending_done()` then closes. A `NO_SAFE_DIFF` execute queues nothing.

## Known external limitations

These are documented limits, not open work:

1. **ReferralCodes** — Agent Import is bulk upload, existing referrals are edited
   through the account UI, and duplicates may be flagged as spam. `Commit` is
   never used speculatively.
2. **ReferralDrop** — no official OAuth or public write API. Manual.
3. **ReferralCode.tv CAPTCHA** — the content save requires a human. No bypass.
4. **ReferralCode.tv Turnstile on GitHub** — the unattended login gate above.
5. **Super-Parrain retry branch** — the already-authenticated-session retry path
   has simply never been exercised by a real run; it is not claimed live-proven.
6. **Hermes live mutation persistence** — deferred to the next legitimate
   operator change. A synthetic mutation must not be run to manufacture proof.

## Validation

```bash
python -m pytest tests -q
```
