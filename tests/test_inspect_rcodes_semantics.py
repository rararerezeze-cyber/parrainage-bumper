"""Agent Import inspect tool never commits."""
from pathlib import Path

from tools.inspect_referralcodes_commit_semantics import OFFICIAL_DOCUMENTATION_REVIEW, OUT


def test_inspector_never_clicks_commit():
    src = Path("tools/inspect_referralcodes_commit_semantics.py").read_text(encoding="utf-8")
    assert "commit_clicked" in src
    assert "Never click Commit" in src
    assert 'has-text("Commit")' not in src
    assert OUT.name == "referralcodes-commit-semantics.json"
    assert "https://referralcodes.com/faq/" in OFFICIAL_DOCUMENTATION_REVIEW["sources"]
    assert "NEVER_AUTO_COMMIT" in OFFICIAL_DOCUMENTATION_REVIEW["conclusion"]
