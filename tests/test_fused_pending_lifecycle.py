import json

import pytest

from lib import super_parrain_schedule as sched


def _seed_pending(path):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "key": "super-parrain:kraken:fr",
                        "platform": "super-parrain",
                        "program": "kraken",
                        "language": "fr",
                        "status": "pending",
                    },
                    {
                        "key": "super-parrain:revolut:fr",
                        "platform": "super-parrain",
                        "program": "revolut",
                        "language": "fr",
                        "status": "pending",
                    },
                    {
                        "key": "parrainage-co:kraken:fr",
                        "platform": "parrainage-co",
                        "program": "kraken",
                        "language": "fr",
                        "status": "pending",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _verified_cycle_report():
    return {
        "bumper_returncode": 0,
        "bumper_stats": {
            "mode": "fused_bumper_canary",
            "canary_content_failed": False,
            "write_status": "WRITE_VERIFIED",
            "autofresh": {
                "details": [
                    {
                        "program": "kraken",
                        "needs_update": True,
                        "fields_filled": ["body"],
                        "skipped": False,
                    }
                ],
                "canary_post_verify": [
                    {
                        "program": "kraken",
                        "ok": True,
                        "post_match": True,
                        "exact_body_match": True,
                        "immutable_ok": True,
                    }
                ],
            },
        },
    }


def _statuses(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["key"]: item for item in data["items"]}


def test_verified_fused_success_closes_only_matching_pending(tmp_path, monkeypatch):
    pending = tmp_path / "pending_writes.json"
    _seed_pending(pending)
    monkeypatch.setattr(sched, "PENDING_PATH", pending)

    closed = sched.close_verified_fused_pending(_verified_cycle_report())

    items = _statuses(pending)
    assert closed == ["super-parrain:kraken:fr"]
    assert items["super-parrain:kraken:fr"]["status"] == "done"
    assert items["super-parrain:kraken:fr"]["done_at"]
    assert items["super-parrain:revolut:fr"]["status"] == "pending"
    assert items["parrainage-co:kraken:fr"]["status"] == "pending"


def test_failed_fused_write_keeps_pending(tmp_path, monkeypatch):
    pending = tmp_path / "pending_writes.json"
    _seed_pending(pending)
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    report = _verified_cycle_report()
    report["bumper_returncode"] = 1

    assert sched.close_verified_fused_pending(report) == []
    assert _statuses(pending)["super-parrain:kraken:fr"]["status"] == "pending"


def test_post_match_false_keeps_pending(tmp_path, monkeypatch):
    pending = tmp_path / "pending_writes.json"
    _seed_pending(pending)
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    report = _verified_cycle_report()
    proof = report["bumper_stats"]["autofresh"]["canary_post_verify"][0]
    proof.update({"ok": False, "post_match": False, "exact_body_match": False})

    assert sched.close_verified_fused_pending(report) == []
    assert _statuses(pending)["super-parrain:kraken:fr"]["status"] == "pending"


def test_non_exact_body_match_keeps_pending(tmp_path, monkeypatch):
    pending = tmp_path / "pending_writes.json"
    _seed_pending(pending)
    monkeypatch.setattr(sched, "PENDING_PATH", pending)
    report = _verified_cycle_report()
    report["bumper_stats"]["autofresh"]["canary_post_verify"][0][
        "exact_body_match"
    ] = False

    assert sched.close_verified_fused_pending(report) == []
    assert _statuses(pending)["super-parrain:kraken:fr"]["status"] == "pending"


@pytest.mark.parametrize(
    "report",
    [
        pytest.param({"bumper_returncode": None}, id="cancelled-before-result"),
        pytest.param(
            {"summary": {"BUMP_CYCLE_24H": "WAIT", "POST_VERIFY": "SKIP"}},
            id="skip",
        ),
    ],
)
def test_cancelled_or_skipped_cycle_keeps_pending(tmp_path, monkeypatch, report):
    pending = tmp_path / "pending_writes.json"
    _seed_pending(pending)
    monkeypatch.setattr(sched, "PENDING_PATH", pending)

    assert sched.close_verified_fused_pending(report) == []
    assert _statuses(pending)["super-parrain:kraken:fr"]["status"] == "pending"
