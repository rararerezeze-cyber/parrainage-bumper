"""Session-wide sandbox: no test may write into the real repo's data/ tree.

Many modules bind a Path constant derived from lib.paths.DATA_DIR (or the
repo ROOT) at import time -- e.g. lib.write_status.STATUS_PATH,
lib.super_parrain_schedule.PENDING_PATH/LAST_SUPER_RUN,
lib.safety.CIRCUIT_PATH, lib.monitor.*.HISTORY_PATH, etc. Because each of
those is `from lib.paths import X` (a separate name bound at import time,
not a live lookup), patching lib.paths.DATA_DIR alone does not redirect
them -- every already-bound constant has to be repointed individually.

Before this fixture existed, running `pytest tests -q` genuinely rewrote
data/platform-write-status.json, data/pending_writes.json,
data/sync-state.json and data/captures/referralcodes-official-dry-run.json
in the real working tree on every run (verified empirically). That is
production state shared with GitHub Actions bots -- a stray local test run
could desynchronize it from what the bots believe on disk.

Strategy: copy data/ (+ last_super_run.txt) once per test session into a
throwaway temp directory, then redirect every known constant there. Real
fixture content (mappings, templates, offers.json, golden files) is still
read correctly because it's a full copy; only writes are diverted, and they
land in a directory that is deleted with the rest of pytest's tmp tree.

Redirection is generic on purpose: for each (module, attribute) pair we take
whatever real path is currently bound, compute it relative to the repo
root, and rebase that same relative path onto the sandbox root. Adding a
new Path constant to some module later only means adding it to
_PATCH_TARGETS below -- no need to hand-write its target location.

Known limitation: this cannot help a module-level `from module import X`
executed at *test collection* time (before this fixture runs), since that
binds a name in the *test* module's own namespace to the pre-patch object.
The only such case in this suite (tests/test_inspect_rcodes_semantics.py
importing OUT) only reads `.name`, never writes -- audited safe. Any new
test doing `from tools.x import SOME_PATH` at module level and then writing
to it would bypass this sandbox; write through the source module (call a
function, or reference `module.SOME_PATH`) instead.
"""
from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (dotted module path, [attribute names]) for every module-level Path
# constant derived from DATA_DIR / repo ROOT that a test could cause to be
# written to.
_PATCH_TARGETS: list[tuple[str, list[str]]] = [
    ("lib.paths", [
        "DATA_DIR", "OFFERS_PATH", "OPERATOR_OVERRIDES_PATH",
        "ACCEPTED_MONITOR_FIELDS_PATH", "SYNC_STATE_PATH",
        "TEMPLATES_DIR", "MAPPINGS_DIR",
    ]),
    ("lib.write_status", ["DATA_DIR", "STATUS_PATH"]),
    ("lib.super_parrain_schedule", [
        "DATA_DIR", "ROOT", "LAST_SUPER_RUN", "PENDING_PATH", "CYCLE_REPORT",
        "JITTER_PATH",
    ]),
    ("lib.safety", ["DATA_DIR", "SNAPSHOT_DIR", "AUDIT_PATH", "CIRCUIT_PATH"]),
    ("lib.notify", ["DATA_DIR", "NOTIFY_DIR", "OUTBOX_PATH", "DEDUP_PATH"]),
    ("lib.canary_gate", ["DATA_DIR", "LOCK_PATH", "PACKS_PATH"]),
    ("lib.hermes_interface", ["DATA_DIR", "OPERATOR_OVERRIDES_PATH", "RESULT_PATH"]),
    ("lib.phase", ["ROOT", "PHASE_PATH"]),
    ("lib.sync_state", ["SYNC_STATE_PATH"]),
    ("lib.operator_overrides", [
        "DATA_DIR", "OPERATOR_OVERRIDES_PATH", "ACCEPTED_MONITOR_FIELDS_PATH",
    ]),
    ("lib.mapping_candidates", ["MAPPING_CANDIDATES_PATH"]),
    ("lib.offers", ["OFFERS_PATH"]),
    ("lib.coverage", ["DATA_DIR", "MAPPINGS_DIR"]),
    ("lib.inventory", ["MAPPINGS_DIR"]),
    ("lib.monitor.auto_accept", [
        "DATA_DIR", "ACCEPTED_MONITOR_FIELDS_PATH", "HISTORY_PATH", "SIM_REPORT",
    ]),
    ("lib.monitor.engine", ["DATA_DIR", "HISTORY_DIR", "REPORT_PATH"]),
    ("lib.monitor.history", ["DATA_DIR", "HISTORY_PATH", "LAST_OBS_PATH"]),
    ("lib.monitor.registry", ["DATA_DIR", "REGISTRY_PATH", "MAPPINGS_DIR"]),
    ("lib.monitor.shadow", ["DATA_DIR", "SHADOW_DIR", "SHADOW_REPORT"]),
    ("tools.telegram_update", ["ROOT", "OPERATOR_OVERRIDES_PATH"]),
    ("tools.run_verified_writers", ["ROOT", "MAPPINGS_DIR"]),
    # Writes data/captures/write-super-parrain-*.json. Unsandboxed, a test that
    # exercises main() overwrote a committed historical capture (including its
    # superseded_by evidence annotation) in the real working tree.
    ("tools.controlled_write_super_parrain", ["ROOT", "OUT_DIR"]),
    ("tools.validate_referralcodes_agent_import", ["ROOT", "OUT"]),
    ("tools.inspect_referralcodes_commit_semantics", ["ROOT", "OUT"]),
    ("platforms.referralcodes.writer", ["ROOT"]),
    # Writes data/captures/referralcodes-agent-import-*.{json,csv}. Before this
    # entry, running the suite rewrote those committed captures in the real
    # working tree (verified: the CSV came back with one CR instead of two).
    ("platforms.referralcodes.agent_import", ["ROOT"]),
]


@pytest.fixture(scope="session", autouse=True)
def _sandbox_production_data(tmp_path_factory):
    sandbox_root = tmp_path_factory.mktemp("autofresh_data_sandbox")
    shutil.copytree(REPO_ROOT / "data", sandbox_root / "data")
    real_last_run = REPO_ROOT / "last_super_run.txt"
    if real_last_run.exists():
        shutil.copy2(real_last_run, sandbox_root / "last_super_run.txt")

    # Two passes: importing a module not yet loaded can itself trigger
    # `from lib.paths import DATA_DIR`-style bindings, which must read the
    # *original* real value, not one already patched by an earlier module in
    # this loop. So resolve every module + its original (pre-patch) values
    # first, then apply all patches together.
    modules = {name: importlib.import_module(name) for name, _ in _PATCH_TARGETS}
    originals: list[tuple[object, str, Path]] = []
    for module_name, attrs in _PATCH_TARGETS:
        mod = modules[module_name]
        for attr in attrs:
            if not hasattr(mod, attr):
                continue
            originals.append((mod, attr, getattr(mod, attr)))

    mp = pytest.MonkeyPatch()
    for mod, attr, current in originals:
        rel = current.relative_to(REPO_ROOT)
        mp.setattr(mod, attr, sandbox_root / rel, raising=True)
    try:
        yield sandbox_root
    finally:
        mp.undo()
