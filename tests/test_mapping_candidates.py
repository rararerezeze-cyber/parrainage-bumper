"""Stages C/D/E: a genuine future observation must never be silently lost
just because merge_conservative_mapping_update() keeps the curated value
active.

    ancienne valeur valide = X   (stage A: curated, protected)
    source future = Y            (stage B: new read-only observation)
    capture READ-ONLY
    -> X reste le mapping actif                    (protection, unchanged)
    -> Y reste visible comme observation candidate  (stage C, this file)
    -> aucune ecriture live
    -> aucune perte de Y
    -> une validation ulterieure peut promouvoir Y proprement (stage E)
"""
from __future__ import annotations

import json

import pytest

from lib import mapping_candidates as mc
from lib.template_builder import merge_conservative_mapping_update


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "mapping-candidates.json"
    monkeypatch.setattr(mc, "MAPPING_CANDIDATES_PATH", path)
    return mc.MappingCandidateStore(path)


def test_divergence_is_detected_and_reported_not_dropped():
    existing = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/OLD/",
        "platform_offer_id": "100408",
        "notes": "curated, not a fresh guess",
    }
    fresh = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/NEW/",  # Y: the site really changed
        "platform_offer_id": "100408",
        "notes": "native_platform_style_only",
    }
    merged, report = merge_conservative_mapping_update(existing, fresh)

    # X remains the active mapping.
    assert merged["edit_url"] == "https://example.test/edit/OLD/"
    # Y is not silently dropped -- it is reported as a divergence.
    assert {"field": "edit_url", "existing": "https://example.test/edit/OLD/", "fresh": "https://example.test/edit/NEW/"} in report["divergences"]


def test_divergence_still_reported_even_on_a_write_verified_frozen_record():
    """A frozen record must still surface a real site change as a
    candidate -- freezing means "never auto-apply", not "never notice"."""
    existing = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/OLD/",
        "notes": "WRITE_VERIFIED 2026-08-13: proven",
    }
    fresh = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/NEW/",
        "notes": "native_platform_style_only",
    }
    merged, report = merge_conservative_mapping_update(existing, fresh)
    assert merged == existing  # totally frozen
    assert any(d["field"] == "edit_url" and d["fresh"] == "https://example.test/edit/NEW/" for d in report["divergences"])


def test_no_divergence_reported_when_fresh_agrees_with_existing():
    existing = {"edit_url": "https://example.test/edit/SAME/", "notes": "x"}
    fresh = {"edit_url": "https://example.test/edit/SAME/", "notes": "x"}
    _, report = merge_conservative_mapping_update(existing, fresh)
    assert report["divergences"] == []


def test_full_scenario_curate_observe_promote(store, tmp_path, monkeypatch):
    """The exact end-to-end scenario requested: X curated -> Y observed ->
    X stays active -> Y survives as a pending candidate -> Y can later be
    promoted deliberately, and only then does the curated mapping change.
    """
    from lib.paths import mapping_path

    mapping_file = tmp_path / "1parrainage.kraken.fr.json"
    monkeypatch.setattr(
        "lib.mapping_candidates.mapping_path",
        lambda platform, program, language: mapping_file,
    )

    existing = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/X/",
        "notes": "curated, human-verified",
    }
    mapping_file.write_text(json.dumps(existing), encoding="utf-8")

    fresh = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/Y/",
        "notes": existing["notes"],  # isolate the edit_url divergence only
    }

    # --- capture READ-ONLY ---
    merged, report = merge_conservative_mapping_update(existing, fresh)
    assert merged["edit_url"] == "https://example.test/edit/X/"  # X stays active
    # No live write: this test never calls anything that saves an
    # announcement, submits a form, or dispatches a workflow -- merge is a
    # pure function and record_candidate_divergence only writes to the
    # local candidate-store JSON.
    for div in report["divergences"]:
        mc.record_candidate_divergence(
            "1parrainage", "kraken", "fr", div["field"], div["existing"], div["fresh"], store=store
        )

    # --- Y is not lost: visible as a pending candidate ---
    pending = mc.list_pending_candidates("1parrainage", "kraken", store=store)
    assert len(pending) == 1
    assert pending[0]["field"] == "edit_url"
    assert pending[0]["curated_value"] == "https://example.test/edit/X/"
    assert pending[0]["observed_value"] == "https://example.test/edit/Y/"
    assert pending[0]["status"] == mc.STATUS_PENDING

    # The real mapping file on disk is still X -- untouched by the capture.
    assert json.loads(mapping_file.read_text(encoding="utf-8"))["edit_url"] == "https://example.test/edit/X/"

    # --- stage D/E: an operator later reviews and promotes Y ---
    result = mc.promote_candidate(
        "1parrainage", "kraken", "fr", "edit_url", actor="test-operator", reason="confirmed site moved", store=store
    )
    assert result["ok"] is True
    assert result["new_value"] == "https://example.test/edit/Y/"

    # Only now does the curated mapping change.
    assert json.loads(mapping_file.read_text(encoding="utf-8"))["edit_url"] == "https://example.test/edit/Y/"
    assert mc.list_pending_candidates("1parrainage", "kraken", store=store) == []


def test_no_divergence_means_the_candidate_store_is_never_touched(store, tmp_path):
    """Cas 1: no divergence -> capture_oneparrainage()'s wiring never calls
    record_candidate_divergence() at all (merge_report["divergences"] is
    empty), so the store file is never created/written -- nothing for
    capture_readonly.yml's `git add data/mapping-candidates.json` to stage,
    so `git diff --cached --quiet` is genuinely quiet: no commit, no noise.
    """
    existing = {"edit_url": "https://example.test/edit/SAME/", "notes": "x"}
    fresh = {"edit_url": "https://example.test/edit/SAME/", "notes": "x"}
    _, report = merge_conservative_mapping_update(existing, fresh)
    assert report["divergences"] == []

    # Faithfully replicate capture_oneparrainage()'s wiring: iterate
    # merge_report["divergences"] and call record_candidate_divergence()
    # for each. With an empty list, the loop body never executes.
    for div in report["divergences"]:
        mc.record_candidate_divergence(
            "p", "prog", "fr", div["field"], div["existing"], div["fresh"], store=store
        )

    assert not store.path.exists(), "store file must not be created when nothing diverged"
    assert mc.list_pending_candidates("p", "prog", store=store) == []


def test_repeated_identical_observation_does_not_spam_the_candidate(store):
    """Cas 3: run suivant -> Y existe toujours -> nouvelle observation Y ->
    compteur/date mis a jour proprement (not a new entry, not lost)."""
    first = mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)
    second = mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)
    entry = mc.list_pending_candidates("p", "prog", store=store)[0]
    assert entry["observation_count"] == 2
    assert entry["observed_value"] == "Y"
    # first_observed_at is the original sighting, never overwritten by a
    # repeat observation; last_observed_at is updated to reflect the latest.
    assert entry["first_observed_at"] == first["first_observed_at"]
    assert entry["last_observed_at"] == second["last_observed_at"]
    assert len(mc.list_pending_candidates("p", "prog", store=store)) == 1  # not duplicated


def test_a_third_value_updates_the_candidate_to_the_latest_observation(store):
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Z", store=store)
    entry = mc.list_pending_candidates("p", "prog", store=store)[0]
    assert entry["observed_value"] == "Z"
    assert entry["observation_count"] == 1


def test_y_is_preserved_in_history_when_superseded_by_z_never_silently_lost(store):
    """X curated -> observation Y -> observation Y again -> observation Z.

    Z becomes the current candidate value, but Y (and the fact it was seen
    twice) must remain fully auditable, not overwritten in place.
    """
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)  # seen again
    entry = mc.list_pending_candidates("p", "prog", store=store)[0]
    assert entry["observed_value"] == "Y"
    assert entry["observation_count"] == 2
    assert entry["history"] == []  # nothing superseded yet

    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Z", store=store)
    entry = mc.list_pending_candidates("p", "prog", store=store)[0]

    # Current candidate is now Z.
    assert entry["observed_value"] == "Z"
    assert entry["observation_count"] == 1
    # X (curated_value) is tracked throughout, unaffected by the Y/Z churn.
    assert entry["curated_value"] == "X"
    # Y is not lost -- archived in append-only history with its own
    # observation window, including that it was seen twice.
    assert len(entry["history"]) == 1
    assert entry["history"][0]["observed_value"] == "Y"
    assert entry["history"][0]["observation_count"] == 2
    assert entry["history"][0]["first_observed_at"] is not None
    assert entry["history"][0]["last_observed_at"] is not None

    # A 4th, distinct observation (W) appends Z to history too --
    # append-only means history keeps growing, never gets clobbered.
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "W", store=store)
    entry = mc.list_pending_candidates("p", "prog", store=store)[0]
    assert entry["observed_value"] == "W"
    assert len(entry["history"]) == 2
    assert [h["observed_value"] for h in entry["history"]] == ["Y", "Z"]


def test_no_auto_promotion_ever_happens(store, tmp_path, monkeypatch):
    """Cas 6: X curated -> Y candidate -> recording (and re-recording) the
    divergence must never itself change the curated mapping. Only an
    explicit promote_candidate() call may do that."""
    mapping_file = tmp_path / "1parrainage.kraken.fr.json"
    monkeypatch.setattr(
        "lib.mapping_candidates.mapping_path", lambda platform, program, language: mapping_file
    )
    mapping_file.write_text(json.dumps({"edit_url": "X"}), encoding="utf-8")

    for _ in range(5):  # repeated observation, still no auto-promotion
        mc.record_candidate_divergence("1parrainage", "kraken", "fr", "edit_url", "X", "Y", store=store)
    assert json.loads(mapping_file.read_text(encoding="utf-8"))["edit_url"] == "X"
    assert mc.list_pending_candidates("1parrainage", "kraken", store=store)[0]["status"] == mc.STATUS_PENDING

    # Only now, an explicit human-triggered call changes the curated mapping.
    result = mc.promote_candidate("1parrainage", "kraken", "fr", "edit_url", actor="human", store=store)
    assert result["ok"] is True
    assert json.loads(mapping_file.read_text(encoding="utf-8"))["edit_url"] == "Y"


def test_write_verified_divergence_is_persisted_as_a_candidate_end_to_end(store):
    """Cas 7: mapping actif reste gele (WRITE_VERIFIED), ET la divergence
    candidate est visible/persistee -- pas seulement dans le report en
    memoire, mais bien ecrite dans le store."""
    existing = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/OLD/",
        "notes": "WRITE_VERIFIED 2026-08-13: proven",
    }
    fresh = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "edit_url": "https://example.test/edit/NEW/",
        "notes": "native_platform_style_only",
    }
    merged, report = merge_conservative_mapping_update(existing, fresh)
    assert merged == existing  # frozen: mapping actif reste gele

    for div in report["divergences"]:
        mc.record_candidate_divergence(
            "1parrainage", "kraken", "fr", div["field"], div["existing"], div["fresh"], store=store
        )

    pending = mc.list_pending_candidates("1parrainage", "kraken", store=store)
    assert any(
        p["field"] == "edit_url"
        and p["curated_value"] == "https://example.test/edit/OLD/"
        and p["observed_value"] == "https://example.test/edit/NEW/"
        for p in pending
    ), "divergence on a WRITE_VERIFIED record must still be persisted as a visible candidate"


def test_promote_requires_a_pending_candidate(store, tmp_path, monkeypatch):
    mapping_file = tmp_path / "m.json"
    mapping_file.write_text(json.dumps({"edit_url": "X"}), encoding="utf-8")
    monkeypatch.setattr(
        "lib.mapping_candidates.mapping_path", lambda platform, program, language: mapping_file
    )
    r = mc.promote_candidate("p", "prog", "fr", "edit_url", actor="t", store=store)
    assert r["ok"] is False
    assert r["error"] == "no_such_candidate"


def test_dismiss_never_touches_the_curated_mapping(store, tmp_path, monkeypatch):
    mapping_file = tmp_path / "m.json"
    mapping_file.write_text(json.dumps({"edit_url": "X"}), encoding="utf-8")
    monkeypatch.setattr(
        "lib.mapping_candidates.mapping_path", lambda platform, program, language: mapping_file
    )
    mc.record_candidate_divergence("p", "prog", "fr", "edit_url", "X", "Y", store=store)
    r = mc.dismiss_candidate("p", "prog", "fr", "edit_url", actor="t", reason="bad extraction", store=store)
    assert r["ok"] is True
    assert mc.list_pending_candidates("p", "prog", store=store) == []
    assert json.loads(mapping_file.read_text(encoding="utf-8"))["edit_url"] == "X"


# --- classification: BUSINESS / TARGETING / ENGINE_METADATA ---
#
# Real incident this section guards against: the first real capture
# produced 116 pending candidates in one run. Only a handful were a real
# code/lien/gain change (BUSINESS); the rest were parser confidence scores,
# an internal quality/sync_mode label, a note, or a URL fragment variant of
# the same announcement (TARGETING/ENGINE_METADATA). An operator queue that
# doesn't separate these trains a human to stop reading it.


def test_classify_business_fields():
    for field in (
        "platform_values.personal_code",
        "platform_values.personal_link",
        "platform_values.referee_reward",
        "platform_values.referrer_reward",
        "platform_values.conditions",
        "personal_code",
        "conditions",
        "title",
    ):
        assert mc.classify_field(field) == mc.CLASS_BUSINESS, field
        assert mc.is_actionable(mc.classify_field(field)) is True


def test_classify_targeting_fields():
    for field in (
        "edit_url",
        "platform_offer_id",
        "occurrences",
        "occurrence_count",
        "edit_url_source",
        "edit_url_learned_at",
        "announcement_url",
    ):
        assert mc.classify_field(field) == mc.CLASS_TARGETING, field
        assert mc.is_actionable(mc.classify_field(field)) is False


def test_classify_engine_metadata_fields():
    for field in (
        "sync_mode",
        "quality",
        "notes",
        "confidences.personal_code",
        "confidences.referee_reward",
    ):
        assert mc.classify_field(field) == mc.CLASS_ENGINE_METADATA, field
        assert mc.is_actionable(mc.classify_field(field)) is False


def test_recorded_candidate_carries_its_class_and_actionable_flag(store):
    biz = mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "platform_values.referee_reward", "200 €", "250 €", store=store
    )
    assert biz["candidate_class"] == mc.CLASS_BUSINESS
    assert biz["actionable"] is True

    meta = mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "confidences.referee_reward", "medium", "low", store=store
    )
    assert meta["candidate_class"] == mc.CLASS_ENGINE_METADATA
    assert meta["actionable"] is False


def test_operator_queue_shows_only_business_actionable(store):
    mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "platform_values.personal_code", "OLD", "NEW", store=store
    )
    mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "sync_mode", "REVIEW", "SAFE_AUTO", store=store
    )
    mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "notes", "old note", "new note", store=store
    )
    mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "confidences.personal_code", "medium", "low", store=store
    )
    mc.record_candidate_divergence(
        "1parrainage", "kraken", "fr", "edit_url", "https://x/old", "https://x/new", store=store
    )

    queue = mc.list_operator_queue("1parrainage", "kraken", store=store)
    assert [c["field"] for c in queue] == ["platform_values.personal_code"]

    diagnostic = mc.list_pending_candidates("1parrainage", "kraken", store=store)
    assert len(diagnostic) == 5, "diagnostic view keeps every class, nothing dropped"

    technical_only = mc.list_pending_candidates(
        "1parrainage", "kraken", candidate_class=mc.CLASS_ENGINE_METADATA, store=store
    )
    assert {c["field"] for c in technical_only} == {"sync_mode", "notes", "confidences.personal_code"}


def test_pre_classification_entries_are_classified_on_read_without_mutating_storage(store):
    """Entries written before candidate_class existed (the real 116 from the
    first capture) must still classify correctly on read, without requiring
    a migration script that rewrites the stored file."""
    data = store.load()
    data["entries"]["1parrainage:kraken:fr:platform_values.personal_link"] = {
        "platform": "1parrainage",
        "program": "kraken",
        "language": "fr",
        "field": "platform_values.personal_link",
        "curated_value": "https://old",
        "observed_value": "https://new",
        "status": mc.STATUS_PENDING,
        "history": [],
    }
    store.save(data)

    queue = mc.list_operator_queue("1parrainage", "kraken", store=store)
    assert len(queue) == 1
    assert queue[0]["candidate_class"] == mc.CLASS_BUSINESS
    assert queue[0]["actionable"] is True

    # The on-disk file itself is untouched -- classification is read-time only.
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert "candidate_class" not in raw["entries"]["1parrainage:kraken:fr:platform_values.personal_link"]


# --- normalization anti-false-positive ---


def test_announcement_url_fragment_only_diff_is_not_a_candidate(store):
    result = mc.record_candidate_divergence(
        "1parrainage",
        "kraken",
        "fr",
        "announcement_url",
        "https://www.1parrainage.com/listeannonces_98906_Adrien89.php#id=100408",
        "https://www.1parrainage.com/listeannonces_98906_Adrien89.php",
        store=store,
    )
    assert result.get("skipped") is True
    assert mc.list_pending_candidates("1parrainage", "kraken", store=store) == []


def test_announcement_url_real_change_still_creates_a_candidate(store):
    result = mc.record_candidate_divergence(
        "1parrainage",
        "kraken",
        "fr",
        "announcement_url",
        "https://www.1parrainage.com/listeannonces_98906_Adrien89.php#id=100408",
        "https://www.1parrainage.com/listeannonces_11111_Other.php#id=999",
        store=store,
    )
    assert result.get("skipped") is not True
    pending = mc.list_pending_candidates("1parrainage", "kraken", store=store)
    assert len(pending) == 1
    assert pending[0]["field"] == "announcement_url"


def test_normalization_never_suppresses_a_real_business_divergence(store):
    """A code/lien/gain change must never be silently swallowed by a
    normalization rule -- even one that happens to look like whitespace or
    formatting noise."""
    result = mc.record_candidate_divergence(
        "1parrainage",
        "kraken",
        "fr",
        "platform_values.personal_code",
        "  cpbrgddy  ",
        "cpbrgddy",
        store=store,
    )
    # BUSINESS fields are never normalized -- even a pure-whitespace
    # difference must still surface, since we cannot be sure it is really
    # whitespace-only for every possible business value.
    assert result.get("skipped") is not True
    pending = mc.list_pending_candidates("1parrainage", "kraken", store=store)
    assert len(pending) == 1
