"""Execute real workflow persistence scripts against a throwaway Git remote."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_two_failed_writer_runs_retain_streak_history_and_partial_baselines(tmp_path):
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("bash unavailable for Actions shell integration")

    remote = tmp_path / "remote.git"
    runner = tmp_path / "runner"

    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    git("init", "--bare", str(remote))
    git("clone", str(remote), str(runner))
    git("switch", "-c", "main", cwd=runner)
    git("config", "user.name", "test", cwd=runner)
    git("config", "user.email", "test@example.invalid", cwd=runner)
    (runner / "initial.txt").write_text("initial")
    git("add", "initial.txt", cwd=runner)
    git("commit", "-m", "initial", cwd=runner)
    git("push", "-u", "origin", "main", cwd=runner)
    steps = yaml.safe_load((ROOT / ".github/workflows/monitor_offers.yml").read_text(encoding="utf-8"))["jobs"]["monitor"]["steps"]
    observe = next(s["run"] for s in steps if s.get("name") == "Persist observations before writers")
    reconcile = next(s["run"] for s in steps if s.get("name") == "Commit monitor state and verified batch baselines")

    def shell(script):
        subprocess.run([bash, "-e", "-c", script.replace("${{ github.ref_name }}", "main")],
                       cwd=runner, check=True, capture_output=True, text=True)

    for streak in (1, 2):
        state = runner / "data/monitor"
        captures = runner / "data/captures"
        state.mkdir(parents=True, exist_ok=True)
        captures.mkdir(parents=True, exist_ok=True)
        (state / "last-observations.json").write_text(json.dumps({"igraal": {"live_high_streak": streak}}))
        with (state / "history.jsonl").open("a") as fh:
            fh.write(json.dumps({"streak": streak}) + "\n")
        (captures / "monitor-last-report.json").write_text(json.dumps({"run_id": str(streak)}))
        shell(observe)
        # Repository-only writer failure simulation. No network/site code runs.
        failed = subprocess.run(["python", "-c", "raise SystemExit(1)"], cwd=runner)
        assert failed.returncode == 1
        (captures / "monitor-auto-accept-batch.json").write_text(json.dumps({"ok": False, "failed_writes": 1}))
        mappings = runner / "data/platform-mappings"
        mappings.mkdir(exist_ok=True)
        (mappings / "verified.json").write_text(json.dumps({"verified_run": streak, "personal_code": "unchanged"}))
        shell(reconcile)
        persisted = json.loads(git("show", "main:data/monitor/last-observations.json", cwd=remote).stdout)
        assert persisted["igraal"]["live_high_streak"] == streak
        history = git("show", "main:data/monitor/history.jsonl", cwd=remote).stdout.splitlines()
        assert len(history) == streak
        assert json.loads(git("show", "main:data/captures/monitor-auto-accept-batch.json", cwd=remote).stdout)["ok"] is False
        baseline = json.loads(git("show", "main:data/platform-mappings/verified.json", cwd=remote).stdout)
        assert baseline == {"verified_run": streak, "personal_code": "unchanged"}


def test_super_verified_baseline_is_committed_without_replaying_platform_cycle(tmp_path):
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("bash unavailable for Actions shell integration")
    remote = tmp_path / "remote.git"
    runner = tmp_path / "runner"

    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    git("init", "--bare", str(remote))
    git("clone", str(remote), str(runner))
    git("switch", "-c", "main", cwd=runner)
    git("config", "user.name", "test", cwd=runner)
    git("config", "user.email", "test@example.invalid", cwd=runner)
    paths = {
        "data/platform-golden/super-parrain/igraal.fr.txt": "historical personal body; reward=old",
        "data/platform-mappings/super-parrain.igraal.fr.json": '{"reward":"old"}',
        "data/pending_writes.json": '[{"program":"igraal"}]',
        "last_super_run.txt": "2026-10-06T00:00:00+00:00",
    }
    for name, value in paths.items():
        p = runner / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
    git("add", ".", cwd=runner)
    git("commit", "-m", "initial", cwd=runner)
    git("push", "-u", "origin", "main", cwd=runner)
    updates = {
        "data/platform-golden/super-parrain/igraal.fr.txt": "historical personal body; reward=new",
        "data/platform-mappings/super-parrain.igraal.fr.json": '{"reward":"new"}',
        "data/pending_writes.json": "[]",
    }
    for name, value in updates.items():
        (runner / name).write_text(value)
    workflow = yaml.safe_load((ROOT / ".github/workflows/bump_super_parrain.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"][next(iter(workflow["jobs"]))]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Commit results")
    script = script.replace("${{ steps.decide.outputs.runtime_mode }}", "NORMAL_BUMP")
    subprocess.run([bash, "-e", "-c", script], cwd=runner, check=True, capture_output=True, text=True)
    assert git("status", "--porcelain", cwd=runner).stdout == ""
    for name, value in updates.items():
        assert git("show", f"main:{name}", cwd=remote).stdout == value
    assert git("show", "main:last_super_run.txt", cwd=remote).stdout == paths["last_super_run.txt"]
