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

import coursework.agents.reactive_agent as ra
from coursework.domain.models import ActionEnum


class DummyClient:
    def __init__(self) -> None:
        self.accepted: list[int] = []
        self.declined: list[int] = []

    def get_positions(self) -> dict:
        return {}

    def accept_tender(self, tender_id: int) -> None:
        self.accepted.append(tender_id)

    def decline_tender(self, tender_id: int) -> None:
        self.declined.append(tender_id)


def test_decide_tender_buy_sets_sell_unwind_and_accepts() -> None:
    decision = ra.ReactiveAgent._decide_tender("BUY", tender_price=9.0, mid=10.0, threshold=0.05)
    assert decision.should_accept is True
    assert decision.unwind_action == ActionEnum.SELL
    assert round(decision.edge, 4) == 0.1


def test_evaluate_tender_declines_and_returns_record() -> None:
    client = DummyClient()
    agent = ra.ReactiveAgent(client=client, threshold=0.10)
    tender = SimpleNamespace(id=101, ticker="CRZY", quantity=1000, price=9.95, action="BUY")
    agent._load_tender_context = lambda _t: (  # type: ignore[method-assign]
        10.0,
        ra.TenderBookMetrics(0, 0, 0, 0.0, 0.0, 0.0, False),
    )

    record = agent.evaluate_tender(tender, tick=12)

    assert record is not None
    assert record.decision == "DECLINED"
    assert record.tender_id == 101
    assert client.accepted == []
    assert client.declined == [101]


def test_evaluate_tender_accepts_and_unwinds() -> None:
    client = DummyClient()
    agent = ra.ReactiveAgent(client=client, threshold=0.01)
    tender = SimpleNamespace(id=202, ticker="CRZY", quantity=1000, price=9.0, action="BUY")
    agent._load_tender_context = lambda _t: (  # type: ignore[method-assign]
        10.0,
        ra.TenderBookMetrics(0, 0, 0, 0.0, 0.0, 0.0, True),
    )

    agent._wait_for_position_after_accept = lambda *_a, **_k: True  # type: ignore[method-assign]

    def fake_unwind_to_baseline(_ticker: str, _baseline: int) -> tuple[float, int, int]:
        return 9.2, 800, 200

    agent._unwind_to_baseline = fake_unwind_to_baseline  # type: ignore[method-assign]

    record = agent.evaluate_tender(tender, tick=20)

    assert record is not None
    assert record.decision == "ACCEPTED"
    assert record.fill_price == 9.2
    assert record.residual == 200
    assert client.accepted == [202]


def test_wait_then_unwind_no_orders_until_settled() -> None:
    """Position updates only after several polls; unwind SELLs in capped chunks."""

    class TrackingClient:
        def __init__(self) -> None:
            self.accepted: list[int] = []
            self.pos = 0
            self._reads_after_accept = 0
            self._accepted = False
            self._settled = False
            self.orders: list[tuple[int, ActionEnum]] = []

        def get_positions(self) -> dict[str, SimpleNamespace]:
            if self._accepted and not self._settled:
                self._reads_after_accept += 1
                if self._reads_after_accept >= 3:
                    self.pos = 1000
                    self._settled = True
            return {"CRZY": SimpleNamespace(position=self.pos)}

        def accept_tender(self, tender_id: int) -> None:
            self.accepted.append(tender_id)
            self._accepted = True

        def decline_tender(self, tender_id: int) -> None:
            pass

        def place_order(self, ticker, order_type, quantity, action, price=None):
            assert self.pos > 0, "must not sell before tender position is visible"
            filled = min(quantity, self.pos)
            self.pos -= filled
            oid = len(self.orders) + 1
            self.orders.append((filled, action))
            return {
                "quantity_filled": filled,
                "quantity_remaining": 0,
                "order_id": oid,
                "vwap": 9.15,
                "price": 9.15,
                "status": "TRANSACTED",
            }

        def get_order(self, order_id: int):
            return None

    client = TrackingClient()
    agent = ra.ReactiveAgent(client=client, threshold=0.01)
    tender = SimpleNamespace(id=303, ticker="CRZY", quantity=1000, price=9.0, action="BUY")
    agent._load_tender_context = lambda _t: (  # type: ignore[method-assign]
        10.0,
        ra.TenderBookMetrics(0, 0, 0, 0.0, 0.0, 0.0, True),
    )

    record = agent.evaluate_tender(tender, tick=1)
    assert record is not None
    assert record.decision == "ACCEPTED"
    assert client.pos == 0
    assert record.residual == 0
    assert sum(q for q, s in client.orders if s == ActionEnum.SELL) == 1000
    assert client._reads_after_accept >= 3


def test_unwind_never_sells_more_than_long_over_baseline() -> None:
    """Pre-existing long + BUY tender: only tender delta is unwound."""

    class MixedClient:
        def __init__(self) -> None:
            self.pos = 500
            self.accepted: list[int] = []
            self.orders: list[int] = []

        def get_positions(self) -> dict[str, SimpleNamespace]:
            return {"CRZY": SimpleNamespace(position=self.pos)}

        def accept_tender(self, tender_id: int) -> None:
            self.accepted.append(tender_id)
            self.pos += 1000

        def decline_tender(self, tender_id: int) -> None:
            pass

        def place_order(self, ticker, order_type, quantity, action, price=None):
            if action == ActionEnum.SELL:
                take = min(quantity, self.pos - 500)
                self.pos -= take
                self.orders.append(take)
                oid = len(self.orders)
                return {
                    "quantity_filled": take,
                    "quantity_remaining": 0,
                    "order_id": oid,
                    "vwap": 10.0,
                    "status": "TRANSACTED",
                }
            return {"quantity_filled": 0, "order_id": 0}

        def get_order(self, order_id: int):
            return None

    client = MixedClient()
    agent = ra.ReactiveAgent(client=client, threshold=0.01)
    tender = SimpleNamespace(id=404, ticker="CRZY", quantity=1000, price=9.0, action="BUY")
    agent._load_tender_context = lambda _t: (  # type: ignore[method-assign]
        10.0,
        ra.TenderBookMetrics(0, 0, 0, 0.0, 0.0, 0.0, True),
    )

    record = agent.evaluate_tender(tender, tick=1)
    assert record is not None
    assert record.decision == "ACCEPTED"
    assert client.pos == 500
    assert sum(client.orders) == 1000
    assert record.residual == 0
