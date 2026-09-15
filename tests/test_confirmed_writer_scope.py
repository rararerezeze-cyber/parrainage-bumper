from __future__ import annotations

import json
from types import SimpleNamespace

from lib.slack_format import render_result
import tools.run_verified_writers as writers


def _fake_plan():
    return SimpleNamespace(
        platform="1parrainage",
        program="demo",
        language="fr",
        historical="CODE=OLD-CODE | LINK=OLD-LINK | REWARD=10 €",
        rendered="CODE=NEW-CODE | LINK=NEW-LINK | REWARD=20 €",
        variables={
            "personal_code": "NEW-CODE",
            "personal_link": "NEW-LINK",
            "referee_reward": "20 €",
        },
        platform_values={
            "personal_code": "OLD-CODE",
            "personal_link": "OLD-LINK",
            "referee_reward": "10 €",
        },
        changed_fields={
            "personal_code": {"old": "OLD-CODE", "new": "NEW-CODE"},
            "personal_link": {"old": "OLD-LINK", "new": "NEW-LINK"},
            "referee_reward": {"old": "10 €", "new": "20 €"},
        },
        mutable_fields=["personal_code", "personal_link", "referee_reward"],
        structure_preserved=True,
    )


def test_scope_plan_keeps_unconfirmed_pending_fields_at_published_values(monkeypatch):
    plan = _fake_plan()
    mapping = SimpleNamespace(
        mutable_fields=["personal_code", "personal_link", "referee_reward"],
        markers={
            "personal_code": "{{CODE}}",
            "personal_link": "{{LINK}}",
            "referee_reward": "{{REWARD}}",
        },
    )
    monkeypatch.setattr(writers.MappingRepository, "load", lambda *_a, **_k: mapping)
    monkeypatch.setattr(
        writers.TemplateRepository,
        "load_text",
        lambda *_a, **_k: "CODE={{CODE}} | LINK={{LINK}} | REWARD={{REWARD}}",
    )
    monkeypatch.setattr(writers, "structure_preserved_via_markers", lambda *_a, **_k: True)

    scoped, note = writers._scope_plan_to_field(plan, "referee_reward")

    assert note is None
    assert scoped is plan
    assert scoped.changed_fields == {
        "referee_reward": {"old": "10 €", "new": "20 €"}
    }
    assert scoped.variables["referee_reward"] == "20 €"
    assert scoped.variables["personal_code"] == "OLD-CODE"
    assert scoped.variables["personal_link"] == "OLD-LINK"
    assert scoped.rendered == "CODE=OLD-CODE | LINK=OLD-LINK | REWARD=20 €"


def test_scope_plan_never_writes_when_confirmed_field_is_not_pending(monkeypatch):
    plan = _fake_plan()
    plan.changed_fields.pop("referee_reward")

    scoped, note = writers._scope_plan_to_field(plan, "referee_reward")

    assert scoped is plan
    assert note == "NO_CONFIRMED_SAFE_DIFF"
    assert scoped.changed_fields == {}


def test_persist_verified_baseline_closes_the_written_state(monkeypatch, tmp_path):
    plan = _fake_plan()
    # Generic/static writer persistence. 1Parrainage intentionally keeps its
    # static golden because the authenticated CKEditor body is authoritative.
    plan.platform = "code-parrainage"
    plan.changed_fields = {
        "referee_reward": {"old": "10 €", "new": "20 €"}
    }
    plan.variables["personal_code"] = "OLD-CODE"
    plan.variables["personal_link"] = "OLD-LINK"
    plan.rendered = "CODE=OLD-CODE | LINK=OLD-LINK | REWARD=20 €"

    golden = tmp_path / "golden.txt"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "platform_values": {
                    "personal_code": "OLD-CODE",
                    "personal_link": "OLD-LINK",
                    "referee_reward": "10 €",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(writers, "golden_path", lambda *_a, **_k: golden)
    monkeypatch.setattr(writers, "mapping_path", lambda *_a, **_k: mapping)
    monkeypatch.setattr(writers, "ROOT", tmp_path)

    out = writers._persist_verified_baseline(plan)

    assert golden.read_text(encoding="utf-8") == plan.rendered
    saved = json.loads(mapping.read_text(encoding="utf-8"))
    assert saved["platform_values"]["referee_reward"] == "20 €"
    assert saved["platform_values"]["personal_code"] == "OLD-CODE"
    assert saved["platform_values"]["personal_link"] == "OLD-LINK"
    assert saved["last_write_at"]
    assert out["golden"] == "golden.txt"


def _slack_result(changed_fields):
    return {
        "ok": True,
        "command": "TotalEnergies gain filleul 50 €",
        "correlation_id": "corr-field-scope",
        "auth": {"identity": "slack:test"},
        "parsed": {
            "action": "set",
            "program": "totalenergies",
            "offer_name": "TotalEnergies",
            "field": "referee_reward",
            "platform": None,
        },
        "result": {
            "action": "set",
            "old_effective": "20 €",
            "new_effective": "50 €",
            "new_source": "GLOBAL_OPERATOR_OVERRIDE",
        },
        "platforms": [
            {
                "platform": "1parrainage",
                "status": "pending_update",
                "can_auto_write": True,
                "route": "AUTO_ON_SAFE_DIFF",
                "changed_fields": changed_fields,
            }
        ],
        "errors": [],
    }


def test_slack_never_offers_confirm_for_unrelated_pending_fields():
    payload = render_result(
        _slack_result(
            {
                "personal_code": {"old": None, "new": "123"},
                "personal_link": {"old": None, "new": "https://example.test"},
            }
        )
    )
    actions = [
        element
        for block in payload["blocks"]
        if block.get("type") == "actions"
        for element in block.get("elements", [])
    ]
    assert not any(a.get("action_id") == "autofresh_confirm_write" for a in actions)
    text = "\n".join(
        b["text"]["text"]
        for b in payload["blocks"]
        if b.get("type") == "section" and b.get("text")
    )
    assert "autre différence à traiter séparément" in text


def test_slack_offers_confirm_when_the_accepted_field_is_really_pending():
    payload = render_result(
        _slack_result(
            {"referee_reward": {"old": "20 €", "new": "50 €"}}
        )
    )
    actions = [
        element
        for block in payload["blocks"]
        if block.get("type") == "actions"
        for element in block.get("elements", [])
    ]
    assert any(a.get("action_id") == "autofresh_confirm_write" for a in actions)


def test_slack_confirmed_result_explains_no_write_needed():
    result = _slack_result(
        {
            "personal_code": {"old": None, "new": "123"},
        }
    )
    result["writers"] = {
        "reports": [
            {
                "platform": "1parrainage",
                "ok": True,
                "note": "NO_CONFIRMED_SAFE_DIFF",
                "confirmed_field": "referee_reward",
            }
        ]
    }
    payload = render_result(result, run_writers_requested=True)
    text = "\n".join(
        b["text"]["text"]
        for b in payload["blocks"]
        if b.get("type") == "section" and b.get("text")
    )
    assert "Résultat de la confirmation" in text
    assert "aucune écriture nécessaire pour ce champ" in text
