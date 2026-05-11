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

    def fake_unwind(_ticker: str, qty: int, _action: ActionEnum) -> tuple[float, int]:
        return 9.2, qty - 800

    agent._unwind = fake_unwind  # type: ignore[method-assign]

    record = agent.evaluate_tender(tender, tick=20)

    assert record is not None
    assert record.decision == "ACCEPTED"
    assert record.fill_price == 9.2
    assert record.residual == 200
    assert client.accepted == [202]
