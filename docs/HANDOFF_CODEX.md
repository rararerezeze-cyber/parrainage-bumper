# HANDOFF — Autofresh / parrainage-bumper

Resume point for the next Codex session. Reconstruct from this file + git + status JSON. Do not restart completed phases.

## Repo / HEAD

- Repo: `rararerezeze-cyber/parrainage-bumper`
- Branch: `autofresh/phase2b-kraken-capture`
- Feature started this finalize at `a683eb3`; Super formalized `5ce5c34`; capture `fdc992e`.
- main HEAD at finalize start: `50877ed` (scheduled Super canary NO_SAFE_DIFF). Do not treat main e2e-status as product truth.
- After this handoff commit, `git log -1 --oneline` is the new HEAD.

## Absolute constraints (still in force)

- Do not break historical bumpers (`bumper.py`, `bump_super_parrain.yml`, `bump_autres.yml`).
- Super-Parrain `CANARY_PENDING` stays intact. `bump_super_parrain.yml` ALWAYS SKIP until `WRITE_VERIFIED`.
- Sole Super-Parrain live-save owner while pending: `activation_canary.yml` (cron `37 5 * * *` and `45 5 * * *` UTC).
- Do not touch `last_super_run.txt` or force the 24h cooldown.
- No extra live write tonight / until Super canary window.
- One authenticated session per platform per cycle. Sequential only.
- Stop on 403 / 429 / CAPTCHA / auth fail / unexpected DOM. No anti-bot bypass.
- No secrets in repo or logs (names only).
- No new global audit. No new architecture.

## CURRENT_STATE

| Platform | Status | Compare | Telegram | Notes |
|---|---|---|---|---|
| super-parrain | CANARY_READY + CANARY_PENDING + SYNC_VERIFIED_NO_SAFE_DIFF | NO_SAFE_DIFF | CANARY_ONLY | Scheduled canary `50877ed` SUCCESS. `changed_fields=none`. No login/save. Not WRITE_VERIFIED. Bumper remains SKIP. Sequence-cleared. |
| parrainage-co | CANARY_READY + SYNC_VERIFIED_NO_SAFE_DIFF | NO_SAFE_DIFF | CANARY_ONLY | Public `offers/113735` already has operator values. Dry-run empty. No fake write. |
| code-parrainage | CANARY_READY | DOM_BLOCKED | CANARY_ONLY | Slider captcha on auth read. Dry-run vs mapping empty. Not SYNC. Skipped so later REAL_SAFE_DIFF can run. |
| 1parrainage | CANARY_READY | DOM_BLOCKED (public still REAL_SAFE_DIFF) | CANARY_ONLY | Public `4jdp7sea` → operator `s5qudqe4`. Live canary `31681603602` STOP on `unexpected_dom: login fields not found on /login`. No save. Do not retry this cycle. Template cleaned (no iGraal HTML leak). |
| referralcodes | CANARY_READY + SYNC_VERIFIED_NO_SAFE_DIFF | NO_SAFE_DIFF | CANARY_ONLY | Native `$200 in Crypto` + `cpbrgddy`. Do not overwrite EN with FR 200 €. |
| referralcode-tv | CANARY_READY | auth+edit proven | CANARY_ONLY | Run `31682310236`: login OK, `add-referral-code/?eid=` is the content edit form (post_content/code/buy_link). Boost `#cliccami` ≠ edit. No Kraken listing — no live write. |
| referraldrop | AUTH_BLOCKED_GOOGLE | — | AUTH_BLOCKED | Google Sign-In. No OAuth bypass. Stay blocked. |

- WRITE_VERIFIED: **0/7**
- Super sequence-cleared: **YES** (SYNC, not WRITE_VERIFIED)
- next-executable: **1parrainage**
- Hermes interface: READY + PRODUCTION_READY (lock + persist_confirmed + idempotency ledger + snapshot)
- Monitor: OBSERVATION_ONLY + SHADOW engine ready (no auto-accept)
- Orchestrator: one-at-a-time; Super SYNC unblocks later; DOM_BLOCKED predecessor may be skipped
- Operator Kraken lock: `cpbrgddy` / `https://invite.kraken.com/JDNW/s5qudqe4` / `200 € en cryptomonnaies`. Never publish `4hpz4gdy` / proinvite / 20 € Bitcoin.

### Goal flags

| Flag | Value |
|---|---|
| ALL_NON_BLOCKED_PLATFORMS_CANARY_READY | **YES** |
| POST_SUPER_CANARIES_ARMED | **YES** |
| MULTIPROGRAM_DRY_RUN_READY | **YES** |
| HERMES_PRODUCTION_READY | **YES** |
| MONITOR_SHADOW_READY | **YES** |

## Architecture (do not replace)

```
Telegram user
  → Hermes (owns bot, allowlist)
  → Autofresh (`tools/hermes_autofresh.py` / `hermes_operator.yml`)
  → JSON (`data/captures/hermes-last-result.json`)
  → Hermes → Telegram

Historical bumpers (content-unrelated bump):
  bump_super_parrain.yml  → CANARY_PENDING → SKIP
  bump_autres.yml         → code + parrainage.co (+ referralcode bump, not content edit)

Content canaries (strict sequence, never parallel):
  1. Super-Parrain     activation_canary.yml (05:37 UTC, owns cooldown) — DONE NO_SAFE_DIFF
  2. parrainage-co     Super sequence-cleared — DONE NO_SAFE_DIFF (public)
  3. code-parrainage   DOM_BLOCKED this cycle (slider) — skipped, not WRITE_VERIFIED
  4. 1parrainage       public REAL_SAFE_DIFF — live STOP unexpected_dom /login (no save)
  5. referralcodes     NO_SAFE_DIFF native $200 — do not execute FR import
  6. referralcode-tv   CANARY_READY (edit form proven). No Kraken listing. No live write this cycle.

  python tools/activation_orchestrator.py next-executable
  python tools/activation_orchestrator.py canary --platform <next>
  # live requires AUTOFRESH_SEQUENCE_LIVE=1
  # or: activation_canary.yml platform=… execute=true (same gate)

Monitor:
  monitor.py --all        → observation only
  monitor.py --shadow     → SHADOW accept/reject/review, never writes offers.json
```

Writers live under `platforms/<id>/writer.py`. Historical `bumper.py` CONFIG must not gain a 1Parrainage runner (config-only secrets are unused unless `TARGET_SITES` includes a runner). 1Parrainage writer reads `ONEPARRAINAGE_*` itself.

## Decisions this finalize session

1. **Do not re-run Super-Parrain.** Honest `NO_SAFE_DIFF` stays `SYNC_VERIFIED_NO_SAFE_DIFF`, never fake `WRITE_VERIFIED`.
2. **Stale write-*-plan.json files that targeted 4hpz4gdy / proinvite / 20 € BTC are poison.** Regenerated. `abort_forbidden_publish` blocks any live payload containing those strings.
3. **Parrainage.co public is already in sync.** Classified NO_SAFE_DIFF / SYNC. No write.
4. **First REAL_SAFE_DIFF is 1Parrainage personal_link only** (`4jdp7sea` → `s5qudqe4`).
5. **ReferralCodes.com native `$200 in Crypto` is already correct.** FR 200 € import is not a safe canary.
6. **activation_canary.yml must commit to the triggering branch**, not always `main`. Dispatch 1P canary with `--ref autofresh/phase2b-kraken-capture`.
7. **main 50877ed canary artifacts were integrated; main e2e-status was not taken as product truth.**

## Decisions previous session

1. **1Parrainage CANARY_READY without live write** — same bar as parrainage-co / code-parrainage: login + edit + save + reread implemented + `tools/controlled_write_1parrainage.py` + `structure_preserved` on kraken. Promoted via `tools/promote_canary_ready.py --platform 1parrainage --apply`.
2. **Login URL is `/login`**, proven public HTTP 200 with email+password fields. Previous capture used `connexion.php` (404) → public fallback. `capture_auth_readonly.py` now tries `/login` first.
3. **Public Kraken offer_id = 100408** (`listeannonces_98906_Adrien89.php#id=100408`). Edit URL still resolved at auth time (member area). Never click Boost / Remonter.
4. **ReferralCode.tv stays WRITE_PREPARED** — public probe re-confirmed 12 listings. GH `capture_readonly.yml` run `31648937632` (READ-ONLY) had `REFERRALCODE_*` present but **login timed out at 30s** (`auth.login=failed`, `edit_urls=[]`). Treat as stop (timeout/unexpected). Do **not** immediately re-login (already used this cycle). Do not promote without `EDIT_URLS_FOUND`.
5. **Orchestrator does not auto-live after Super PASS.** `sequence-after-super` prepares remaining queue only when Super is WRITE_VERIFIED. `--execute` also requires `AUTOFRESH_SEQUENCE_LIVE=1`.
6. **Hermes success only after disk confirm.** `persist_confirmed` is required for `ok=true` on set/remove. File lock serializes mutating commands. Idempotency ledger replays identical set/remove. Snapshot of overrides/status before mutation.
7. **Monitor SHADOW never writes.** `SHADOW_ACCEPT` is a recommendation only (`auto_applied=false`, `would_write=false`). Human must accept later.
8. **Circuit breakers** trip on 403/429/CAPTCHA/auth/unexpected DOM. They do not open Super-Parrain cooldown.

## Secrets (names only — never values)

| Name | Used by |
|---|---|
| SUPER_PARRAIN_EMAIL / SUPER_PARRAIN_PASSWORD | Super-Parrain canary / bumper |
| PARRAINAGE_CO_EMAIL / PARRAINAGE_CO_PASSWORD / PARRAINAGE_CO_RM_COOKIE | Parrainage.co (cookie preferred) |
| CODE_PARRAINAGE_EMAIL / CODE_PARRAINAGE_PASSWORD | Code-Parrainage |
| ONEPARRAINAGE_EMAIL / ONEPARRAINAGE_PASSWORD | 1Parrainage |
| REFERRALCODE_EMAIL / REFERRALCODE_PASSWORD | ReferralCode.tv (no S) |
| REFERRALCODES_EMAIL / REFERRALCODES_PASSWORD | ReferralCodes.com Agent Import |
| TWOCAPTCHA_KEY | Historical slider / Turnstile (parrainage.co password fallback only) |
| AUTOFRESH_OPERATOR_TOKEN / HERMES_SHARED_TOKEN | Hermes → Autofresh |
| AUTOFRESH_ALLOW_LOCAL_OPERATOR | Local CLI only (`=1`) |

Local env on this machine: **all unset**. GH Actions secrets are the source.

## Workflows

| Workflow | Role | Do not |
|---|---|---|
| `activation_canary.yml` | Scheduled Super-Parrain canary 05:37/05:45 UTC; manual other platforms | Force cooldown; live Super write before eligible |
| `bump_super_parrain.yml` | Historical bump — SKIP while CANARY_PENDING | Commit `last_super_run.txt` on skip path |
| `bump_autres.yml` | Historical code + parrainage.co bump | Content canary / Super |
| `hermes_operator.yml` | Hermes backend | Treat as BotFather bot |
| `monitor_offers.yml` | Observation only; commit on business change | Auto-accept / live write |
| `capture_readonly.yml` | Auth READ-ONLY capture + edit probes | Save/boost |
| `controlled_write.yml` | Super-Parrain plan/execute (legacy) | Use while CANARY_PENDING |

Concurrency: Super canary + Super bumper share `parrainage-bumper-super`.

## Key files added/changed

- `platforms/oneparrainage/writer.py` — full pipeline
- `tools/controlled_write_1parrainage.py`
- `tools/probe_1parrainage_edit.py`
- `tools/multiprogram_dry_run.py`
- `tools/activation_orchestrator.py` — `sequence-after-super`, `multiprogram-dry-run`
- `lib/hermes_interface.py` — lock, persist_confirmed, ledger
- `lib/operator_overrides.py` — atomic save + re-read
- `lib/safety.py` — snapshot / rollback / audit / circuits
- `lib/canary_gate.py` — sequential one-at-a-time predecessor PASS
- `tools/controlled_write_referralcodes.py`
- `lib/monitor/shadow.py` — SHADOW engine
- `monitor.py --shadow`
- `data/circuit-breakers.json`
- `data/platform-write-status.json` — 1Parrainage CANARY_READY
- Tests: `tests/test_safety_shadow_hermes.py` (+ hermes persist asserts)

## Tests

```
python -m pytest tests -q
```

Last run this session: **99 passed**.

Useful targeted:

```
python tools/promote_canary_ready.py --platform 1parrainage
python tools/controlled_write_1parrainage.py --program kraken
python tools/probe_1parrainage_edit.py --public
python tools/probe_referralcode_tv_edit.py --public
python tools/multiprogram_dry_run.py
python tools/activation_orchestrator.py status
python tools/activation_orchestrator.py sequence-after-super
python tools/e2e_status.py
python monitor.py --shadow
```

## Blockers

1. **Super-Parrain not WRITE_VERIFIED** — wait for `activation_canary.yml` at ~05:37 UTC. Do not live-write Super before eligible. Do not run historical bumper.
2. **ReferralCode.tv auth/edit unproven** — secrets exist in GH but last READ-ONLY probe (`31648937632`, commit `222960c`) failed: `Timeout 30000ms exceeded` on login. Do not re-run immediately (session already used). Next cycle: inspect `data/captures/referralcode-tv-edit-map.json` `auth` + `referralcode-tv-raw.txt` / screenshots in the run artifact, then one new `--auth` probe with longer timeout only if the log is a slow page (not CAPTCHA/403/429). Promote only if `EDIT_URLS_FOUND`.
3. **No local secrets** — cannot prove 1Parrainage member edit URL or RCTV edit URL on this machine.
4. **WRITE_VERIFIED 0/7** — Telegram live path stays off.

## Exact next action (Codex)

Do this in order. Stop if any step is 403/429/CAPTCHA/auth/unexpected DOM.

1. `git fetch && git checkout autofresh/phase2b-kraken-capture && git pull && git log -1 --oneline`
2. Confirm Super still `CANARY_PENDING` and `last_super_run.txt` unchanged. If `now >= 2026-08-13T05:37:10Z`, inspect latest `activation_canary.yml` run artifacts (`data/captures/activation-canary-result.json`). Do **not** start a second Super live write if one is in flight or already attempted this window.
3. If Super canary `post_match=true` → `WRITE_VERIFIED` / `NORMAL_BUMP` → fire **exactly one** next platform:
   ```
   python tools/activation_orchestrator.py next-executable
   # expect next=parrainage-co
   AUTOFRESH_SEQUENCE_LIVE=1 python tools/activation_orchestrator.py canary --platform parrainage-co
   # or workflow_dispatch activation_canary.yml platform=parrainage-co execute=true
   ```
   Wait for that PASS. Then the next (`code-parrainage`), never two at once. Packs already exist in `data/captures/canary-pack-*.json`. Do not rebuild writers.
4. If Super still pending: **do not re-login RCTV** (timeout already used this cycle). Post-Super platforms are armed — nothing to configure.
5. Optional READ-ONLY 1Parrainage edit URL proof (`sites=oneparrainage`) to store real `edit_url` for kraken `100408`. Not required for CANARY_READY; required before first live 1Parrainage canary.
6. Do not auto-accept SHADOW decisions. Do not live-write 1Parrainage / RCTV / ReferralCodes tonight.

## Status JSON to trust

- `data/platform-write-status.json`
- `data/autofresh-phase.json` (`super_parrain_runtime=CANARY_PENDING`)
- `data/captures/e2e-status.json`
- `data/captures/activation-orchestrator-status.json`
- `data/captures/activation-sequence-after-super.json`
- `data/captures/multiprogram-dry-run.json`
- `data/captures/post-super-canary-packs.json`
- `data/captures/canary-pack-parrainage-co.json` (and code-parrainage / 1parrainage / referralcodes)
- `data/captures/monitor-shadow-report.json`
- `data/circuit-breakers.json`
- `last_super_run.txt`

## What not to redo

- Super-Parrain CANARY_PENDING gate / shared concurrency
- Parrainage.co / Code-Parrainage / ReferralCodes CANARY_READY pipelines
- Hermes interface contract (extend, do not replace)
- Monitor observation engine (SHADOW sits on top)
- ReferralDrop Google block
- Historical bumper rewrite
