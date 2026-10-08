import pytest

from platforms.referralcode_tv.encoding import TextRepair, needs_repair, repair_text
from platforms.referralcode_tv.writer import execute_write


def corrupt(text):
    return "".join(bytes([b]).decode("cp1252") if b not in (0x81, 0x8d, 0x8f, 0x90, 0x9d)
                   else chr(b) for b in text.encode("utf-8"))


@pytest.mark.parametrize("good", [
    "⚽️ SPECIAL WORLD CUP OFFER – ⭐️ Get up to €150",
    "🎟️ ⚙️ ℹ️ 💬 — I’ll guide you • ⸻",
    "é è à ç œ 中文 😀", "€50 + €100 refund; minimum €10",
])
def test_real_sequences_and_idempotence(good):
    assert repair_text(corrupt(good)) == good
    assert repair_text(repair_text(corrupt(good))) == good
    assert repair_text(good) == good


def test_mixed_unicode_double_encoding_and_ascii_fields():
    link = "https://www.unibet.fr/inscription/?campaign=280526&parrain=03356181D410779A"
    good = f'<p>⚽️ café – €150</p><a href="{link}">{link}</a>'
    mixed = f'中文 😀 {corrupt(corrupt(good))}'
    assert repair_text(mixed) == f'中文 😀 {good}'


def test_invalid_sequences_preserved_and_loss_refused():
    assert repair_text("Ã word Â alone â text") == "Ã word Â alone â text"
    with pytest.raises(ValueError, match="manual review"):
        repair_text("lost � emoji")
    assert needs_repair("lost � emoji")


def test_exact_source_guard():
    plan = TextRepair.prepare(corrupt("⭐️"), corrupt("€150"))
    plan.verify_source(plan.title_before, plan.body_before)
    with pytest.raises(ValueError, match="recapture"):
        plan.verify_source(plan.title_before, "operator edited this")
    assert (plan.title_after, plan.body_after) == ("⭐️", "€150")


def test_no_unattended_save_enabled():
    assert execute_write()["write_mode"] == "HUMAN_SAVE_REQUIRED"


def test_corrupt_template_cannot_produce_prepared_content(monkeypatch):
    from types import SimpleNamespace
    from platforms.referralcode_tv import writer

    ref = SimpleNamespace(platform="referralcode-tv", program="unibet", language="en")
    monkeypatch.setattr(writer, "list_mapping_refs", lambda: [ref])
    monkeypatch.setattr(writer, "_load_edit_map", lambda: {})
    monkeypatch.setattr(writer, "OffersRepository", lambda: SimpleNamespace(get_by_slug=lambda _: {}))
    monkeypatch.setattr(writer, "apply_effective_to_offer", lambda offer, **_: offer)
    monkeypatch.setattr(writer, "MappingRepository", lambda: SimpleNamespace(load=lambda *args: ref))
    monkeypatch.setattr(writer, "TemplateRepository", lambda: SimpleNamespace(
        exists=lambda *args: True, load_text=lambda *args: corrupt("€150")))
    monkeypatch.setattr(writer, "Renderer", lambda _: SimpleNamespace(render=lambda text, *args, **kwargs: text))
    plan = writer.build_write_plan("unibet")
    assert plan.programs[0]["status"] == "encoding_corrupt"
    assert plan.programs[0]["action"] == "BLOCKED"
    assert plan.live is False
