from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_DIR = _ROOT / "agents"
for _p in (_ROOT, _AGENTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import deliberative_agent as da


class DummyClient:
    def __init__(self) -> None:
        self.accepted: list[int] = []
        self.declined: list[int] = []

    def accept_tender(self, tender_id: int) -> None:
        self.accepted.append(tender_id)

    def decline_tender(self, tender_id: int) -> None:
        self.declined.append(tender_id)


def test_compute_tender_decision_declines_on_negative_expected_pnl() -> None:
    tender_input = da.TenderInput(
        tender_id=1,
        ticker="CRZY",
        quantity=1000,
        tender_price=10.0,
        expiry_tick=200,
        action_str="BUY",
        tender_is_buy=True,
    )
    snapshot = da.TenderMarketSnapshot(
        mid=10.0,
        bid_depth_total=1000,
        ask_depth_total=1000,
        n_bid_levels=2,
        best_bid=9.99,
        best_ask=10.01,
        slip=100.0,
        book_covers_full_qty=True,
    )

    decision = da.DeliberativeAgent._compute_tender_decision(tender_input, snapshot, risk_blocked=False)
    assert decision.decline_reason == "E[pnl] <= 0"
    assert decision.expected_pnl < 0
    assert decision.liability_safe is True


def test_compute_tender_decision_declines_when_liability_not_safe() -> None:
    tender_input = da.TenderInput(
        tender_id=2,
        ticker="CRZY",
        quantity=1000,
        tender_price=10.0,
        expiry_tick=200,
        action_str="BUY",
        tender_is_buy=True,
    )
    snapshot = da.TenderMarketSnapshot(
        mid=10.1,
        bid_depth_total=300,
        ask_depth_total=1000,
        n_bid_levels=1,
        best_bid=10.0,
        best_ask=10.2,
        slip=float("inf"),
        book_covers_full_qty=False,
    )
    decision = da.DeliberativeAgent._compute_tender_decision(tender_input, snapshot, risk_blocked=False)
    assert decision.liability_safe is False
    assert decision.decline_reason == "liability safety gate"


def test_evaluate_tender_declines_when_risk_blocked() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    tender = SimpleNamespace(id=11, ticker="CRZY", quantity=500, price=9.8, expiry_tick=250, action="BUY")

    agent._load_market_snapshot = lambda _t: da.TenderMarketSnapshot(  # type: ignore[method-assign]
        mid=10.0,
        bid_depth_total=1000,
        ask_depth_total=1000,
        n_bid_levels=1,
        best_bid=9.95,
        best_ask=10.05,
        slip=1.0,
        book_covers_full_qty=True,
    )
    agent._projected_exposure_breach = lambda *_args, **_kwargs: True  # type: ignore[method-assign]

    record = agent.evaluate_tender(tender, tick=100)

    assert record is not None
    assert record.decision == "DECLINED"
    assert record.risk_blocked is True
    assert client.declined == [11]


def test_evaluate_tender_accepts_and_runs_unwind() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    tender = SimpleNamespace(id=12, ticker="CRZY", quantity=500, price=9.5, expiry_tick=250, action="BUY")

    agent._load_market_snapshot = lambda _t: da.TenderMarketSnapshot(  # type: ignore[method-assign]
        mid=10.0,
        bid_depth_total=1000,
        ask_depth_total=1000,
        n_bid_levels=1,
        best_bid=9.95,
        best_ask=10.05,
        slip=1.0,
        book_covers_full_qty=True,
    )
    agent._projected_exposure_breach = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    agent._start_unwind = lambda *_args, **_kwargs: SimpleNamespace(done=True, used_limit_orders=False)  # type: ignore[method-assign]
    agent._run_unwind_to_completion = lambda rec: None  # type: ignore[method-assign]

    record = agent.evaluate_tender(tender, tick=100)

    assert record is not None
    assert record.decision == "ACCEPTED"
    assert client.accepted == [12]


def test_apply_pnl_record_no_liability_violation() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    rec = da.TenderRecord(
        tick=1,
        tender_id=101,
        ticker="CRZY",
        action="BUY",
        quantity=1000,
        tender_price=10.0,
        mid_price=10.0,
        edge=0.0,
        decision="ACCEPTED",
    )
    unwind = da.UnwindState(
        tender_id=101,
        ticker="CRZY",
        quantity=1000,
        tender_price=10.0,
        tender_was_buy=True,
        unwind_action=da.ActionEnum.SELL,
        unwind_value_accum=10500.0,
        unwind_qty_accum=1000,
        done=True,
    )

    agent._position_qty = lambda _ticker: 0  # type: ignore[method-assign]
    agent._get_mid = lambda _ticker: 10.5  # type: ignore[method-assign]

    agent._apply_pnl_record(rec, unwind)

    assert rec.pnl == 500.0
    assert rec.commission_cost == 20.0
    assert rec.liability_violation_volume == 0
    assert rec.liability_fine == 0.0
    assert rec.net_pnl == 480.0


def test_apply_pnl_record_applies_liability_violation_fine() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    rec = da.TenderRecord(
        tick=1,
        tender_id=102,
        ticker="CRZY",
        action="BUY",
        quantity=1000,
        tender_price=10.0,
        mid_price=10.0,
        edge=0.0,
        decision="ACCEPTED",
    )
    unwind = da.UnwindState(
        tender_id=102,
        ticker="CRZY",
        quantity=1000,
        tender_price=10.0,
        tender_was_buy=True,
        unwind_action=da.ActionEnum.SELL,
        unwind_value_accum=12000.0,
        unwind_qty_accum=1200,
        done=True,
    )

    agent._position_qty = lambda _ticker: 0  # type: ignore[method-assign]
    agent._get_mid = lambda _ticker: 12.0  # type: ignore[method-assign]

    agent._apply_pnl_record(rec, unwind)

    assert rec.pnl == 2000.0
    assert rec.commission_cost == 24.0
    assert rec.liability_violation_volume == 200
    assert rec.liability_fine == 78.0
    assert rec.net_pnl == 1898.0


def test_allowed_liability_qty_enforces_reduce_only() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    agent._position_qty = lambda _ticker: 2500  # type: ignore[method-assign]
    assert agent._allowed_liability_qty("CRZY", da.ActionEnum.SELL) == 2500
    assert agent._allowed_liability_qty("CRZY", da.ActionEnum.BUY) == 0


def test_allowed_liability_qty_uses_tender_liability_when_position_lags() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    agent._position_qty = lambda _ticker: 0  # type: ignore[method-assign]
    unwind = da.UnwindState(
        tender_id=500,
        ticker="TAME",
        quantity=70000,
        tender_price=25.0,
        tender_was_buy=False,
        unwind_action=da.ActionEnum.BUY,
        liability_remaining=70000,
    )
    assert agent._allowed_liability_qty("TAME", da.ActionEnum.BUY, unwind=unwind) == 70000


def test_pulse_unwind_waits_when_liability_remaining_but_no_position_signal() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    unwind = da.UnwindState(
        tender_id=501,
        ticker="TAME",
        quantity=70000,
        tender_price=25.0,
        tender_was_buy=False,
        unwind_action=da.ActionEnum.BUY,
        liability_remaining=70000,
    )
    agent._unwind = unwind
    agent._position_qty = lambda _ticker: 0  # type: ignore[method-assign]
    agent._open_limit_ids = lambda _ticker: []  # type: ignore[method-assign]
    agent._best_bb = lambda _ticker: (25.0, 25.1)  # type: ignore[method-assign]
    agent._infer_tick = lambda _bb, _ba: 0.01  # type: ignore[method-assign]
    agent._place_limit_slices = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
    agent._cancel_limits = lambda _ids: None  # type: ignore[method-assign]
    agent._ticks_remaining_session = lambda _case: 200  # type: ignore[method-assign]

    agent._pulse_unwind({"tick": 100})

    assert agent._unwind is not None
    assert agent._unwind.done is False
    assert agent._unwind.liability_remaining == 70000


def test_apply_pnl_record_does_not_impute_mid_fill_when_no_execution() -> None:
    client = DummyClient()
    agent = da.DeliberativeAgent(client=client)
    rec = da.TenderRecord(
        tick=1,
        tender_id=103,
        ticker="TAME",
        action="SELL",
        quantity=70000,
        tender_price=25.36,
        mid_price=25.20,
        edge=0.0,
        decision="ACCEPTED",
    )
    unwind = da.UnwindState(
        tender_id=103,
        ticker="TAME",
        quantity=70000,
        tender_price=25.36,
        tender_was_buy=False,
        unwind_action=da.ActionEnum.BUY,
        unwind_value_accum=0.0,
        unwind_qty_accum=0,
        liability_remaining=70000,
        done=True,
    )
    agent._position_qty = lambda _ticker: 0  # type: ignore[method-assign]

    agent._apply_pnl_record(rec, unwind)

    assert rec.fill_price == 0.0
    assert rec.pnl == 0.0
    assert rec.commission_cost == 0.0
    assert rec.net_pnl == 0.0
