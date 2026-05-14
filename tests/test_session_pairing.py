"""Tests for RIT session fingerprint pairing."""

from __future__ import annotations

from pathlib import Path

import pytest

from coursework.agents.session_pairing import (
    ScenarioFingerprint,
    fingerprints_match,
    load_fingerprint,
    save_fingerprint,
)


def test_fingerprints_match_mids():
    a = ScenarioFingerprint("LT3", 1, 0, {"CRZY": 10.0, "TAME": 25.0})
    b = ScenarioFingerprint("LT3", 2, 0, {"CRZY": 10.005, "TAME": 25.0})
    assert fingerprints_match(a, b, mid_eps=0.02)
    assert not fingerprints_match(a, b, mid_eps=0.001)


def test_save_load_roundtrip(tmp_path: Path):
    fp = ScenarioFingerprint("LT3", 3, 0, {"CRZY": 12.34, "TAME": None})
    path = tmp_path / "r_1.json"
    save_fingerprint(path, fp)
    got = load_fingerprint(path)
    assert got.case_name == fp.case_name
    assert got.period == fp.period
    assert got.mids["CRZY"] == pytest.approx(12.34)
    assert got.mids.get("TAME") is None
