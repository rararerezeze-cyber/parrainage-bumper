# AutoFresh handoff

Current repository handoff for `rararerezeze-cyber/parrainage-bumper` on `main`.
Do not reconstruct current state from older run artifacts. The authority order is:

1. `data/platform-write-status.json` for platform capability and proof status;
2. `data/autofresh-phase.json` for runtime phase and authorization flags;
3. `data/pending_writes.json` for unresolved write lifecycle;
4. the most recent named capture referenced by the status record.

`docs/RELEASE-STATE.md` is the short operator-facing summary of all of the
below (platform table, autonomy classes, workflow classification, known
external limitations). It is a view of the state files, never an authority
over them.

## Current platform state

| Platform | Status | Durable constraint |
|---|---|---|
| Super-Parrain | `WRITE_VERIFIED` | Latest real fused cycle is GitHub run `32369146793`: 39/39 saves and `POST_VERIFY=PASS`; Kraken `post_match`, `exact_body_match`, and `immutable_ok` are true. The already-authenticated-session retry branch was not exercised and remains not live-proven. |
| Parrainage.co | `WRITE_VERIFIED` | GitHub headless canary and rollback proven. |
| Code-Parrainage | `WRITE_VERIFIED` | GitHub headless canary and rollback proven. |
| 1Parrainage | `WRITE_VERIFIED` | `PC_OFF_READY`: GitHub run `32559662078` proves the complete unattended chain with exactly two actual Save clicks, account plus full-public canary verification, exact source/normalized rollback, and public marker removal. `gh_headless_save=PROVEN`. Duplicate run `32559814742` was refused before browser startup with zero writes. |
| ReferralCodes | `CANARY_READY` | `NEVER_AUTO_COMMIT`. Validate-only evidence exists; Agent Import is not a documented update path for an existing referral. |
| ReferralCode.tv | `WRITE_VERIFIED` | `HUMAN_SAVE_REQUIRED` because save requires CAPTCHA. No bypass. |
| ReferralDrop | `AUTH_BLOCKED_MANUAL` | No official public write path. No auth bypass. |

The current count is 5/7 `WRITE_VERIFIED`. This count does not mean all five are
eligible for unattended mutation; platform-specific constraints above still apply.

## 1Parrainage headless evidence

The repository contains a dedicated manual workflow:
`.github/workflows/canary_write_1parrainage.yml`.

It is intentionally separate from the normal canary promotion ladder because
1Parrainage was already `WRITE_VERIFIED`; it existed only to prove
`gh_headless_save`. The proof is now complete and the gate is permanently closed
for this evidence probe.

- manual `workflow_dispatch` confirmation `WRITE_1P_CANARY_ROLLBACK`;
- current platform status still `WRITE_VERIFIED`;
- `gh_headless_save` was still exactly `NOT_RUN` before the successful run;
- closed circuit and exclusive canary lock;
- no business `SAFE_DIFF` and exact Kraken identity invariants.

Run `32559662078` used one browser session and exactly two actual Save clicks:
append a unique body-only marker, verify it in the account and public
full-detail `#desc_detail` view, restore the exact original CKEditor HTML, then
verify both the fresh server-backed source hash and the deterministic
CKEditor-normalized hash plus public marker removal. Every invariant passed and
`gh_headless_save=PROVEN` is persisted. The canonical capture is the current
authority; run `32559814742` was a duplicate dispatch refused by the already-
PROVEN gate before browser startup, with zero Save clicks and zero platform writes.

The harness records requested phases, resolved controls, click starts, and click
completions separately. A missing or ambiguous control is zero actual Save clicks
and cannot trigger rollback. Once a click invocation starts, persistence is
possible and rollback becomes mandatory. The Save selector remains scoped to the
single edit form and exact `Envoyer`/`Valider` controls. The proven daily-quota
message is classified as `prewrite_blocked: daily_edit_quota_exhausted`; it is not
treated as selector drift and does not justify broadening the selector.

Run `32410815698` reached both real Saves and restored the public Kraken content,
but the old verifier read only the explicitly truncated list excerpt and the
immediate CKEditor reread differed by one byte. Read-only diagnostic run
`32414266066` proved the full public body is available at
`/detail_parrain.php?par=98906&offre=100408#desc_detail`. It also proved that the
fresh account body is still exactly 1062 bytes with SHA-256 `ad2a57ac...`, while
`CKEDITOR.setData(current)->getData()` adds only one terminal LF and is then
byte-idempotent at 1063 bytes / `48d1ad78...`. The canary verifier uses both
exact representations; semantic equivalence is not substituted for exactness.

Do not dispatch this workflow without explicit same-exchange operator approval:
it performs two real platform saves even though the final business content is
restored.

## ReferralCodes conclusion

GitHub run `32265735051` performed Validate only and never clicked Commit.
Official documentation now closes the useful read-only uncertainty:

- Agent Import is presented as bulk upload followed by Validate and Commit;
- existing referrals are changed with the account Edit action;
- only one referral per shop/app is allowed, duplicates are rejected, and
  repeated duplicate submissions may be treated as spam.

Therefore Commit must not be used speculatively as an update mechanism. Keep the
existing native Kraken `$200` listing and `NEVER_AUTO_COMMIT`. Evidence and source
URLs are stored in `data/captures/referralcodes-commit-semantics.json`.

## Runtime ownership and pauses

Hermes owns the Telegram operator interface. This repository exposes the
AutoFresh command workflow and result contract but does not contain the Hermes
plugin runtime. The installed standalone Hermes plugin was corrected and
live-confirmed on 2026-08-22: the real Telegram → `MessageEvent` → plugin path
preserved the complete 28-character multi-word test value, emitted the expected
privacy-safe digest trace, and displayed the confirmation screen. The old
`/autofresh_valeur` "Valeur vide" issue is closed; no fake guard was added to
this repository and the Hermes core was not changed.

The tester accidentally clicked the final confirmation. The resulting set run
`32563740744` invoked no writer and made no platform write. Its test override
remained only on the ephemeral runner because the evidence commit was blocked
by the runner-only audit log, so `origin/main`'s override file stayed unchanged.
The repository workflow now stages durable mutation snapshots and circuit-
breaker state, stashes only `data/audit/events.jsonl`, and refuses every other
residual tracked or untracked path before rebasing. A synthetic mutation must
not be repeated merely to prove persistence; the next legitimate operator
change can provide that final live confirmation.

Do not change `monitor_auto_accept`. Monitor decisions remain observation-only
unless the existing explicit operator flow authorizes otherwise.

## Scheduled bump health

GitHub read-only run `32565187359` proved the current remaining ReferralCode.tv
blocker: GitHub-hosted Chromium receives a real Cloudflare Turnstile security
interstitial before the login form (`Un instant…`, hidden
`cf-turnstile-response`, no email field). This is not selector drift or cookie
consent and must not be bypassed. The runtime detects it before login, performs
no retry, and records `CAPTCHA_OR_ANTIBOT`. Local headed human operation remains
the supported path for any RCTV *content save*.

ReferralCode.tv is no longer part of the combined scheduled target. Its bump now
runs alone in `.github/workflows/bump_referralcode_tv.yml`
(`BUMP_ISOLATED_BEST_EFFORT`): its own concurrency group, its own
`TARGET_SITES`, only the ReferralCode.tv credentials, no CAPTCHA-solver key, and
a cron offset from `bump_autres.yml`. It can never delay, cancel, or fail the
healthy Code-Parrainage/Parrainage.co cycle.

The cycle rules live in `lib/rctv_bump.py` and are covered by
`tests/test_rctv_isolated_bump.py`: normal login, then at most one `#cliccami`
boost per run when quota allows; an unparseable listings page is never treated
as "quota available"; a missing boost control is an observation, not a failure.
When the standalone Turnstile gate appears, `bumper.ExpectedExternalBlocker` stops
the cycle before any credential is submitted, and `main()` separates it from real
failures so the run stays green. A permanently red workflow on a known,
documented, unfixable-from-GitHub gate is noise that would hide a real
regression; network, DOM and credential failures still fail the run loudly.

No legitimate unattended GitHub login is available: the challenge precedes the
login form, so there is no session to obtain without solving it. This is a
`KNOWN_LIMITATION`, not an open task, and no cookie or session may be invented,
extracted, or committed to reach around it.

## Observability

`lib/notify.py` is the single, centralized event contract. AutoFresh still has no
Telegram bot of its own — Hermes owns Telegram:

```
AutoFresh runtime → lib.notify.emit() → data/notifications/outbox.jsonl
                  → Hermes (local plugin) → Telegram
```

Events are allow-listed (routine `NO_CHANGE` cycles and polls are dropped by
construction), deduplicated per event with a TTL, scrubbed against a closed field
whitelist, and `BEST_EFFORT` / `FAIL_OPEN`: `emit()` never raises, so a broken
notification path can never fail a bump or a business write. Emitters are wired
into circuit breakers, rollback, status promotion, canary failure, the pending
lifecycle, the monitor's candidate divergences, and the ReferralCode.tv cycle.

`data/notifications/` is gitignored — runner-only, no commit noise — and both
residual-path gates (`tools/check_hermes_evidence_paths.py`,
`tools/check_1parrainage_evidence_paths.py`) treat it as transient so an emitted
event can never turn a successful mutation into a red workflow. All seven
production workflows upload it as the `autofresh-notifications-*` artifact.

Because that directory is gitignored, a runner starts with no memory of what was
already reported, so each production workflow also restores and saves
`data/notifications/dedup.json` via a rolling `actions/cache` key
(`autofresh-notify-dedup-<workflow>-<run_id>` with a prefix `restore-keys`). That
is what makes the 24 h TTL real across runs rather than only within one. The scope
is **per workflow**: two workflows keep independent state, and two concurrent runs
of one workflow can both emit — one duplicate notification, never a suppressed
one. Both cache steps are `continue-on-error` so the BEST_EFFORT contract holds.

Read it with `python tools/notify_digest.py --since-hours 24` or
`--daily-summary`. **Not in this repository:** the Hermes plugin that fetches
that artifact and relays the records to Telegram. It was not modified from here
and no second production bot was created.

## Workflow classification

`data/workflow-registry.json` classifies every workflow (production vs archived
proof) and `tests/test_workflow_registry.py` fails if one is added, removed, or
reclassified. Closed canaries are kept as historical proof and are refused at
runtime, not merely by convention: `may_execute_canary()` returns
`already_WRITE_VERIFIED` for the four verified platforms,
`guard_live_evidence_probe()` refuses the 1Parrainage probe because
`gh_headless_save` is `PROVEN` (demonstrated by run `32559814742`), and
`platforms.referralcodes.writer.execute_write(dry_run=False)` returns
`NEVER_AUTO_COMMIT` before reaching any Commit code.

## Super-Parrain cycle ownership

`activation_canary.yml` is manual-only since 2026-08-24. Its two daily schedules
existed solely to make it the live saver while Super-Parrain was `CANARY_PENDING`.
That is over: the platform is `WRITE_VERIFIED`, the historical bumper is
authorized, the runtime is `NORMAL_BUMP`, and `FUSED_UPDATE_BUMP` in
`bump_super_parrain.yml` owns the production cycle alone. A scheduled canary could
only have re-entered a live-write path on a platform whose proof is complete.

Two guards now back that up:

- `lib.super_parrain_schedule.super_parrain_canary_allowed()` refuses a
  Super-Parrain canary while the platform is `WRITE_VERIFIED` + `NORMAL_BUMP`;
  `activation_canary.yml` passes `--canary` so an accidental dispatch stops with
  zero write, zero Save and zero pending. `controlled_write.yml` is unaffected —
  it stays the legitimate explicit operator write path.
- `tools/controlled_write_super_parrain.py` no longer queues a pending on
  `--execute` before the plan is built. It used to, so every early return
  (missing `--force`, active cooldown, and above all `NO_SAFE_DIFF`) stranded an
  open pending that nothing on that path closed — a phantom pending waiting to
  happen on the first eligible slot with no real diff. A pending is now opened
  only where a real content diff exists.

## Remaining gaps

1. ReferralCodes: durable product limitation, not an invitation to test Commit.
   Revisit only if an official existing-referral Edit path or update API becomes
   available.
2. ReferralCode.tv: GitHub-hosted unattended login is blocked by the proven
   Cloudflare Turnstile interstitial; the human CAPTCHA save remains required.
   The isolated bump workflow handles that gate as an expected external blocker
   and stays healthy; this is a durable limitation, not open work.
3. ReferralDrop: manual authentication/write-path blocker remains.
4. Hermes mutation persistence: repository and installed-plugin fixes are
   complete; live persistence after the workflow fix is intentionally deferred
   to the next legitimate operator change, not a synthetic override.
5. Inventory metadata: `parrainage-co/paypal` is an intentional historical
   tombstone (`NOT_PRESENT_ON_ACCOUNT`, no edit URL, never write or recreate),
   not an active stale target. ReferralCode.tv's account inventory contains
   Whatnot, but no authenticated edit EID is proven in the persisted captures.
   GitHub cannot currently refresh that inventory because the same Turnstile
   gate appears before login; resolve the EID only through a legitimate local
   headed human read-only session, never by inventing a mapping or bypassing
   the challenge.

## Validation

Run the complete repository suite with:

```powershell
python -m pytest tests -q
```

No cancelled, skipped, dry-run, or attempted workflow is proof of a platform
write. Status promotion requires the persisted post-verification invariants.
