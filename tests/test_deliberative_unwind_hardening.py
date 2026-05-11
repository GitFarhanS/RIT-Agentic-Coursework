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

from coursework.agents.deliberative import ActionEnum, DeliberativeAgent, Unwind


class FakeClient:
    def __init__(self) -> None:
        self.accepted: list[int] = []
        self._position = 0

    def get_positions(self):
        return {"CRZY": SimpleNamespace(position=self._position)}

    def accept_tender(self, tender_id: int) -> None:
        self.accepted.append(tender_id)
        self._position += 500

    def decline_tender(self, _tender_id: int) -> None:
        return None

    def get_securities_book(self, _ticker: str, limit: int = 20):
        lvl = SimpleNamespace(price=10.0, quantity_remaining=10_000, quantity=10_000)
        return SimpleNamespace(bids=[lvl], asks=[lvl])

    def get_case(self):
        return {"tick": 100, "status": "RUNNING", "ticks_per_period": 300, "total_periods": 1, "period": 1}

    def place_order(self, *_args, **_kwargs):
        return {"order_id": 1, "quantity_filled": 7, "vwap": 10.01}

    def get_order(self, _order_id: int):
        return {"order_id": 1, "quantity_filled": 7, "quantity_remaining": 0, "status": "TRANSACTED"}

    def get_orders(self, status: str = "OPEN"):
        return []

    def cancel_order(self, _order_id: int):
        return {}

    def get_securities(self, ticker: str):
        return [{"ticker": ticker, "bid": 10.0, "ask": 10.1}]


def test_evaluate_captures_baseline_before_accept() -> None:
    client = FakeClient()
    client._position = 1_000
    agent = DeliberativeAgent(client=client)
    tender = SimpleNamespace(id=1, ticker="CRZY", quantity=500, price=9.9, expiry_tick=200, action="BUY")

    agent._risk_blocks = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    captured: dict[str, int] = {}

    def _start_unwind(_tid, _ticker, _qty, _price, _is_buy, baseline_pos):
        captured["baseline"] = baseline_pos
        return Unwind(
            tender_id=1,
            ticker="CRZY",
            qty=500,
            tender_price=9.9,
            is_buy_tender=True,
            side=ActionEnum.SELL,
            baseline_pos=baseline_pos,
        )

    agent.start_unwind = _start_unwind  # type: ignore[method-assign]
    agent._run_unwind_loop = lambda _u: None  # type: ignore[method-assign]

    rec = agent.evaluate(tender, tick=100)

    assert rec is not None
    assert rec.decision == "ACCEPTED"
    assert captured["baseline"] == 1_000


def test_remaining_liability_returns_reverse_side_on_overshoot() -> None:
    client = FakeClient()
    agent = DeliberativeAgent(client=client)
    u = Unwind(
        tender_id=2,
        ticker="CRZY",
        qty=500,
        tender_price=10.0,
        is_buy_tender=True,
        side=ActionEnum.SELL,
        baseline_pos=100,
    )
    client._position = 90

    qty, side = agent.remaining_liability(u)

    assert qty == 10
    assert side == ActionEnum.BUY


def test_pulse_urgency_cancels_and_markets_to_current_liability() -> None:
    client = FakeClient()
    agent = DeliberativeAgent(client=client)
    u = Unwind(
        tender_id=3,
        ticker="CRZY",
        qty=500,
        tender_price=10.0,
        is_buy_tender=True,
        side=ActionEnum.SELL,
        baseline_pos=100,
    )
    calls: list[tuple] = []
    agent.remaining_liability = lambda _uw: (250, ActionEnum.BUY)  # type: ignore[method-assign]
    agent.cancel_all_and_wait = lambda ticker, timeout=2.0: calls.append(("cancel", ticker, timeout))  # type: ignore[method-assign]
    agent.market_to_target = lambda uw, qty, side: calls.append(("market", qty, side))  # type: ignore[method-assign]

    case = {"tick": 250, "status": "RUNNING", "ticks_per_period": 300, "total_periods": 1, "period": 1}
    agent.pulse(u, case)

    assert calls[0][0] == "cancel"
    assert calls[1] == ("market", 250, ActionEnum.BUY)


def test_run_unwind_requires_two_consecutive_zero_polls(monkeypatch) -> None:
    client = FakeClient()
    agent = DeliberativeAgent(client=client)
    u = Unwind(
        tender_id=4,
        ticker="CRZY",
        qty=500,
        tender_price=10.0,
        is_buy_tender=True,
        side=ActionEnum.SELL,
        baseline_pos=100,
    )
    seq = iter(
        [
            (0, ActionEnum.SELL),
            (5, ActionEnum.SELL),
            (0, ActionEnum.SELL),
            (0, ActionEnum.SELL),
        ]
    )
    agent.remaining_liability = lambda _uw: next(seq)  # type: ignore[method-assign]
    pulse_calls = {"n": 0}
    agent.pulse = lambda uw, _case: pulse_calls.__setitem__("n", pulse_calls["n"] + 1)  # type: ignore[method-assign]
    agent.reconcile_to_baseline = lambda _uw, timeout=5.0: None  # type: ignore[method-assign]
    agent.cancel_all_and_wait = lambda _ticker, timeout=2.0: None  # type: ignore[method-assign]
    agent.client.get_case = lambda: {"status": "RUNNING", "tick": 10}  # type: ignore[method-assign]

    import coursework.agents.deliberative.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod.time, "sleep", lambda _s: None)

    agent._run_unwind_loop(u)

    assert pulse_calls["n"] == 3


def test_market_chunk_uses_order_reported_fill_qty() -> None:
    client = FakeClient()
    agent = DeliberativeAgent(client=client)
    u = Unwind(
        tender_id=5,
        ticker="CRZY",
        qty=500,
        tender_price=10.0,
        is_buy_tender=True,
        side=ActionEnum.SELL,
        baseline_pos=100,
    )

    filled = agent._market_chunk(u, 10, ActionEnum.SELL)

    assert filled == 7
    assert u.fills_qty == 7
