from __future__ import annotations

from dataclasses import dataclass, field

from coursework.domain.models import ActionEnum


@dataclass
class TenderRecord:
    """One tender offer: decision, reasoning, and P&L outcome."""
    tick: int
    tender_id: int
    ticker: str
    action: str
    quantity: int
    tender_price: float
    mid_price: float = 0.0
    edge: float = 0.0
    edge_signed: float = 0.0
    decision: str = ""
    decline_reason: str = ""
    estimated_slippage: float = 0.0
    spread_captured: float = 0.0
    txn_cost: float = 0.0
    expected_pnl: float = 0.0
    risk_blocked: bool = False
    avg_unwind_price: float = 0.0
    gross_pnl: float = 0.0
    commission_cost: float = 0.0
    liability_violation_volume: int = 0
    liability_fine: float = 0.0
    net_pnl: float = 0.0
    residual: int = 0


@dataclass
class SessionStats:
    """Summary stats for one game session."""
    session: int
    seen: int = 0
    accepted: int = 0
    declined: int = 0
    total_gross_pnl: float = 0.0
    total_net_pnl: float = 0.0
    records: list[TenderRecord] = field(default_factory=list)


@dataclass
class Unwind:
    """Tracks an active unwind job (flatten position back to baseline)."""
    tender_id: int
    ticker: str
    qty: int
    tender_price: float
    is_buy_tender: bool
    side: ActionEnum
    baseline_pos: int
    fills_value: float = 0.0
    fills_qty: int = 0
    last_reprice_tick: int = -10**9
