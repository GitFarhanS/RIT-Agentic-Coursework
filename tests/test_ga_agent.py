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

import coursework.agents.ga_agent as ga


class DummyClient:
    def __init__(self) -> None:
        self.accepted: list[int] = []
        self.declined: list[int] = []

    def accept_tender(self, tender_id: int) -> None:
        self.accepted.append(tender_id)

    def decline_tender(self, tender_id: int) -> None:
        self.declined.append(tender_id)

    def get_securities_book(self, _ticker: str, limit: int = 20):
        _ = limit
        level = SimpleNamespace(price=10.0, quantity_remaining=10_000, quantity=10_000)
        return SimpleNamespace(bids=[level], asks=[level])


def test_ga_agent_declines_when_below_threshold(monkeypatch) -> None:
    client = DummyClient()
    agent = ga.GAAgent(client=client)
    tender = SimpleNamespace(id=31, ticker="CRZY", quantity=1000, price=10.0, expiry_tick=250, action="BUY")

    monkeypatch.setattr(ga, "get_mid_price", lambda _client, _ticker: 10.0)

    record = agent.evaluate(tender, tick=100)

    assert record is not None
    assert record.decision == "DECLINED"
    assert client.declined == [31]


def test_ga_agent_accepts_when_gates_pass(monkeypatch) -> None:
    client = DummyClient()
    agent = ga.GAAgent(client=client)
    tender = SimpleNamespace(id=32, ticker="CRZY", quantity=1000, price=9.5, expiry_tick=250, action="BUY")

    monkeypatch.setattr(ga, "get_mid_price", lambda _client, _ticker: 10.0)
    delegated = ga.base.TenderRecord(
        tick=100,
        tender_id=32,
        ticker="CRZY",
        action="BUY",
        quantity=1000,
        tender_price=9.5,
        decision="ACCEPTED",
    )
    monkeypatch.setattr(ga.base.DeliberativeAgent, "evaluate", lambda _self, _tender, _tick: delegated)

    record = agent.evaluate(tender, tick=100)

    assert record is not None
    assert record.decision == "ACCEPTED"
    assert client.accepted == []


def test_ga_post_passive_respects_slice_ratio() -> None:
    client = DummyClient()
    from coursework.agents.deliberative.settings import DeliberativeSettings

    settings = DeliberativeSettings.from_ga_params(
        {
            "market_order_ratio": 0.6,
            "time_decay_factor": 1.0,
            "slice_size_ratio": 0.2,
            "threshold": 0.0,
        }
    )
    agent = ga.GAAgent(client=client, settings=settings, edge_threshold=0.0)
    u = ga.base.Unwind(
        tender_id=999,
        ticker="CRZY",
        qty=500,
        tender_price=10.0,
        is_buy_tender=True,
        side=ga.base.ActionEnum.SELL,
        baseline_pos=0,
    )
    placed: list[tuple[int, ga.base.ActionEnum, float]] = []
    agent.remaining_liability = lambda _uw: (500, ga.base.ActionEnum.SELL)  # type: ignore[method-assign]
    agent.resting_qty = lambda _ticker, _side: 0  # type: ignore[method-assign]
    agent.best_bid_ask = lambda _ticker: (10.0, 10.1)  # type: ignore[method-assign]
    agent.client.place_order = (  # type: ignore[method-assign]
        lambda _ticker, _otype, qty, side, price=None: placed.append((qty, side, float(price)))
    )

    agent.post_passive(u, 500)

    assert placed
    assert sum(q for q, _, _ in placed) == 500
