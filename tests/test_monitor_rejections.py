"""Reject an observed monitor candidate without editing a listing."""
from lib.monitor import rejections
from lib.monitor.rejections import is_rejected_candidate, reject_candidate
from lib.offers import OffersRepository
from tools.telegram_update import parse_message


def test_exact_value_rejection_is_durable_and_scoped(tmp_path):
    path = tmp_path / "reject.json"
    assert not is_rejected_candidate("nrj-mobile", "referee_reward", "4,99 €", path=path)
    reject_candidate("nrj-mobile", "referee_reward", "4,99 €", path=path)
    reject_candidate("nrj-mobile", "referee_reward", "4,99 €", path=path)
    assert is_rejected_candidate("nrj-mobile", "referee_reward", "4,99 €", path=path)
    assert is_rejected_candidate("nrj-mobile", "referee_reward", " 4,99   € ", path=path)
    assert not is_rejected_candidate("nrj-mobile", "referee_reward", "9 €", path=path)
    assert not is_rejected_candidate("nrj-mobile", "referrer_reward", "4,99 €", path=path)
    assert not is_rejected_candidate("other", "referee_reward", "4,99 €", path=path)
    import json
    assert len(json.loads(path.read_text(encoding="utf-8"))["rejections"]) == 1


def test_slack_refusal_command_resolves_only_specific_field():
    parsed = parse_message("nrj-mobile refuser gain filleul 4,99 €", OffersRepository())
    assert parsed["action"] == "reject"
    assert parsed["program"] == "nrj-mobile"
    assert parsed["field"] == "referee_reward"
    assert parsed["value"] == "4,99 €"


def test_public_monitor_auto_accept_respects_operator_rejection(tmp_path, monkeypatch):
    from lib.monitor.auto_accept import evaluate_field
    path = tmp_path / "reject.json"
    reject_candidate("nrj-mobile", "referee_reward", "4,99 €", path=path)
    monkeypatch.setattr(rejections, "REJECTIONS_PATH", path)
    cand = {"program": "nrj-mobile", "field": "referee_reward",
            "canonical": "10 €", "observed": "4,99 €"}
    verdict = evaluate_field(cand, None)
    assert verdict["decision"] == "REJECT"
    assert "operator_rejected_observed_value" in verdict["reasons"]
