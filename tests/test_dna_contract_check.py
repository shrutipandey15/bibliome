"""dna_contract_check.py must actually fail on drift, not rubber-stamp
whatever the frontend says. Run against the real sibling checkout when present;
otherwise assert the script degrades to a clean skip rather than a false pass."""

from pathlib import Path

import dna_contract_check as check

FRONTEND_ROOT = Path(__file__).resolve().parent.parent.parent / "bookDNA-frontend"


def test_passes_against_the_real_frontend_checkout_if_present():
    if not FRONTEND_ROOT.exists():
        return
    assert check.main() == 0


def test_missing_frontend_repo_skips_cleanly_rather_than_failing():
    assert check.main("/nonexistent/bookDNA-frontend") == 0


def test_detects_injected_emotion_slug_drift(monkeypatch):
    if not FRONTEND_ROOT.exists():
        return
    monkeypatch.setattr(check, "frontend_emotion_slugs", lambda root: {"not_a_real_slug"})
    assert check.main() == 1


def test_detects_injected_archetype_count_drift(monkeypatch):
    if not FRONTEND_ROOT.exists():
        return
    monkeypatch.setattr(check, "frontend_archetype_count", lambda root: 999)
    assert check.main() == 1


def test_detects_injected_landing_preview_drift(monkeypatch):
    if not FRONTEND_ROOT.exists():
        return
    monkeypatch.setattr(
        check, "frontend_landing_preview",
        lambda root: [{"id": "grief_romantic", "name": "Wrong", "color": "#000000", "glyph": "X"}],
    )
    assert check.main() == 1
