from __future__ import annotations

import sys
from pathlib import Path

from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_AGENTS_DIR = _ROOT / "agents"
for _p in (_SRC, _ROOT, _AGENTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from coursework.agents.deliberative import DeliberativeAgent, DeliberativeSettings


def test_deliberative_settings_defaults() -> None:
    s = DeliberativeSettings()
    assert s.poll_interval == 0.5
    assert s.order_cap("CRZY") == 25_000
    assert s.passive_slice_ratio == 1.0


def test_deliberative_settings_from_ga_merges_ratios() -> None:
    params = {
        "market_order_ratio": 0.55,
        "time_decay_factor": 0.9,
        "slice_size_ratio": 0.4,
        "threshold": 0.02,
    }
    s = DeliberativeSettings.from_ga_params(params)
    assert s.market_ratio == 0.55
    assert s.urgency_ticks == 90
    assert s.passive_slice_ratio == 0.4


def test_evaluate_declines_expired_tender() -> None:
    class C:
        def get_case(self):
            return {}

        def decline_tender(self, tid: int) -> None:
            pass

        def get_securities(self, ticker=None):
            return [{"bid": 10.0, "ask": 10.2}]

        def get_securities_book(self, ticker, limit=20):
            lvl = SimpleNamespace(price=10.0, quantity_remaining=5000, quantity=5000)
            return SimpleNamespace(bids=[lvl], asks=[lvl])

    agent = DeliberativeAgent(C(), DeliberativeSettings())
    tender = SimpleNamespace(id=1, ticker="CRZY", quantity=100, price=9.9, expiry_tick=50, action="BUY")
    rec = agent.evaluate(tender, tick=100)
    assert rec is not None
    assert rec.decision == "DECLINED"
    assert rec.decline_reason == "expired"
