from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass

# enums


class ActionEnum(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    def __str__(self) -> str:
        return str(self.value)


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

    def __str__(self) -> str:
        return str(self.value)


class CaseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    CANCELLED = "CANCELLED"
    TRANSACTED = "TRANSACTED"


# dataclasses for better typing and attribute access
@dataclass
class Case:
    """Current case/session info from get_case()."""
    name: str
    tick: int
    status: str  # ACTIVE | PAUSED | STOPPED
    period: int
    ticks_per_period: int
    total_periods: int
    is_enforce_trading_limits: bool = True

    @classmethod
    def from_api(cls, d: dict) -> "Case":
        return cls(
            name=d.get("name", ""),
            tick=int(d.get("tick", 0) or 0),
            status=str(d.get("status", "STOPPED")),
            period=int(d.get("period", 0) or 0),
            ticks_per_period=int(d.get("ticks_per_period", 0) or 0),
            total_periods=int(d.get("total_periods", 0) or 0),
            is_enforce_trading_limits=bool(d.get("is_enforce_trading_limits", True)),
        )


@dataclass
class Trader:
    """Logged-in trader from get_trader()."""
    trader_id: str
    first_name: str
    last_name: str
    nlv: float

    @classmethod
    def from_api(cls, d: dict) -> "Trader":
        return cls(
            trader_id=str(d.get("trader_id", "")),
            first_name=str(d.get("first_name", "")),
            last_name=str(d.get("last_name", "")),
            nlv=float(d.get("nlv", 0) or 0),
        )


@dataclass
class TradingLimit:
    """Trading/exposure limit from get_limits()."""
    name: str
    gross: float
    net: float
    gross_limit: int
    net_limit: int
    gross_fine: float = 0.0
    net_fine: float = 0.0

    @classmethod
    def from_api(cls, d: dict) -> "TradingLimit":
        return cls(
            name=str(d.get("name", "")),
            gross=float(d.get("gross", 0) or 0),
            net=float(d.get("net", 0) or 0),
            gross_limit=int(d.get("gross_limit", 0) or 0),
            net_limit=int(d.get("net_limit", 0) or 0),
            gross_fine=float(d.get("gross_fine", 0) or 0),
            net_fine=float(d.get("net_fine", 0) or 0),
        )


@dataclass
class News:
    """News item from get_news()."""
    news_id: int
    headline: str
    body: str
    period: int
    tick: int
    ticker: str

    @classmethod
    def from_api(cls, d: dict) -> "News":
        return cls(
            news_id=int(d.get("news_id", 0) or 0),
            headline=str(d.get("headline", "")),
            body=str(d.get("body", "")),
            period=int(d.get("period", 0) or 0),
            tick=int(d.get("tick", 0) or 0),
            ticker=str(d.get("ticker", "")),
        )


@dataclass
class Position:
    ticker: str
    position: int
    vwap: float
    last: float
    unrealised: float
    realised: float

    @classmethod
    def from_api(cls, d: dict):
        return cls(
            ticker=d["ticker"],
            position=d["position"],
            vwap=d["vwap"],
            last=d["last"],
            unrealised=d["unrealized"],
            realised=d["realized"],
        )


@dataclass
class TenderOrder:
    id: int
    ticker: str
    quantity: int
    action: ActionEnum
    price: float
    tick: int
    expiry_tick: int

    @classmethod
    def from_api(cls, d: dict):
        return cls(
            id=d["tender_id"],
            ticker=d["ticker"],
            quantity=d["quantity"],
            action=d["action"],
            price=d["price"],
            tick=d["tick"],
            expiry_tick=d["expires"],
        )


@dataclass
class Security:
    ticker: str
    vwap: float
    nlv: float
    last: float
    bid: float
    bid_size: float
    ask: float
    ask_size: float
    volume: int
    description: str
    min_price: float
    max_price: float
    trading_fee: float
    min_size: float
    max_size: float

    @classmethod
    def from_api(cls, d: dict):
        return cls(
            ticker=d.get("ticker", ""),
            vwap=float(d.get("vwap", 0.0) or 0.0),
            nlv=float(d.get("nlv", 0.0) or 0.0),
            last=float(d.get("last", 0.0) or 0.0),
            bid=float(d.get("bid", 0.0) or 0.0),
            bid_size=float(d.get("bid_size", d.get("bidSize", 0.0)) or 0.0),
            ask=float(d.get("ask", 0.0) or 0.0),
            ask_size=float(d.get("ask_size", d.get("askSize", 0.0)) or 0.0),
            volume=int(d.get("volume", 0) or 0),
            description=d.get("description", ""),
            min_price=float(d.get("min_price", d.get("minPrice", 0.0)) or 0.0),
            max_price=float(d.get("max_price", d.get("maxPrice", 0.0)) or 0.0),
            trading_fee=float(d.get("trading_fee", d.get("tradingFee", 0.0)) or 0.0),
            min_size=float(d.get("min_trade_size", d.get("minSize", 0.0)) or 0.0),
            max_size=float(d.get("max_trade_size", d.get("maxSize", 0.0)) or 0.0),
        )


@dataclass
class Level:
    id: int
    tick: int
    trader_id: str
    ticker: str
    quantity: int
    price: float
    order_type: OrderType
    action: ActionEnum
    quantity_filled: int
    quantity_remaining: int
    vwap: float
    status: str

    @classmethod
    def from_api(cls, d: dict):
        order_id = int(d.get("order_id", 0) or 0)
        tick = int(d.get("tick", d.get("period", 0)) or 0)
        trader_id = str(d.get("trader_id", ""))
        ticker = str(d.get("ticker", ""))
        quantity = int(d.get("quantity", 0) or 0)
        price = float(d.get("price", 0.0) or 0.0)

        order_type_raw = d.get("type")
        try:
            order_type = (
                OrderType(order_type_raw)
                if order_type_raw is not None
                else OrderType.LIMIT
            )
        except ValueError:
            order_type = OrderType.LIMIT

        action_raw = d.get("action")
        try:
            action = (
                ActionEnum(action_raw) if action_raw is not None else ActionEnum.BUY
            )
        except ValueError:
            action = ActionEnum.BUY

        quantity_filled = int(d.get("quantity_filled", 0) or 0)
        quantity_remaining = quantity - quantity_filled
        vwap_val = d.get("vwap")
        vwap = float(vwap_val) if (vwap_val is not None) else 0.0
        status = str(d.get("status", ""))

        return cls(
            id=order_id,
            tick=tick,
            trader_id=trader_id,
            ticker=ticker,
            quantity=quantity,
            price=price,
            order_type=order_type,
            action=action,
            quantity_filled=quantity_filled,
            quantity_remaining = quantity_remaining,
            vwap=vwap,
            status=status,
        )

@dataclass
class Order(Level):
    parent_order_id: Optional[int] = None
    parent_order_tick: Optional[int] = None
    grandparent_order_id: Optional[int] = None
    grandparent_order_tick: Optional[int] = None

    @classmethod
    def from_api(cls, d: dict) -> "Order":
        level = Level.from_api(d)
        def _int_or_none(key: str):
            v = d.get(key)
            return int(v) if v is not None else None
        return cls(
            id=level.id,
            tick=level.tick,
            trader_id=level.trader_id,
            ticker=level.ticker,
            quantity=level.quantity,
            price=level.price,
            order_type=level.order_type,
            action=level.action,
            quantity_filled=level.quantity_filled,
            quantity_remaining=level.quantity_remaining,
            vwap=level.vwap,
            status=level.status,
            parent_order_id=_int_or_none("parent_order_id"),
            parent_order_tick=_int_or_none("parent_order_tick"),
            grandparent_order_id=_int_or_none("grandparent_order_id"),
            grandparent_order_tick=_int_or_none("grandparent_order_tick"),
        )

@dataclass
class OrderBook:
    bids: List[Level]
    asks: List[Level]


@dataclass
class EvaluatedTender:
    passed: bool
    reason: str
    tender_order: TenderOrder 
    edge: float 
    violates_exposure: bool 
    available_depth: float
    staleness: float # this is how old the average level is in the book
    feasable_levels: int

    real_available_depth: Optional[float] = None

@dataclass
class UnwindTelemetry:
    unwind_pnl: float
    cost_of_execution: float
    unwind_time: int
    limits_placed: int
    instant_fill_ratio: float
    limits_cancelled: int
    repricing_events: int