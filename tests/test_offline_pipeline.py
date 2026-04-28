"""Smoke tests for offline aggregation / fit / synthetic / fitness pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "synthetic_model_params.json"


def test_synthetic_model_exists_or_skip() -> None:
    if not MODEL.exists():
        pytest.skip("Run scripts/fit_distributions.py to create data/synthetic_model_params.json")


def test_generator_session_length() -> None:
    if not MODEL.exists():
        pytest.skip("missing model json")
    from synthetic.generator import SyntheticSessionGenerator

    gen = SyntheticSessionGenerator(MODEL)
    states = gen.generate_session(seed=123)
    assert len(states) == 300
    b = states[0].books["CRZY"]
    assert len(b.bids) == 20 and len(b.asks) == 20
    assert b.bids[0][0] > b.bids[1][0]
    assert b.asks[0][0] < b.asks[1][0]


def test_fitness_returns_float() -> None:
    if not MODEL.exists():
        pytest.skip("missing model json")
    from ga.fitness import evaluate

    v = evaluate({"threshold": 0.02}, model_path=MODEL, n_sessions=1, base_seed=0)
    assert isinstance(v, float) and v == v  # finite
