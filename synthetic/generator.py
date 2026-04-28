"""
Generate synthetic sessions (ticks 0..299) from data/synthetic_model_params.json.

Books respect L2 ordering: bid levels strictly decreasing in price, asks strictly
increasing; spread is positive. Price bounds match LT3-style ranges from tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from scipy.stats import lognorm

BOOK_LEVELS = 20
DEFAULT_MODEL_REL = Path(__file__).resolve().parent.parent / "data" / "synthetic_model_params.json"


@dataclass
class SyntheticTender:
    tender_id: int
    ticker: str
    action: str
    quantity: int
    price: float


@dataclass
class BookSnapshot:
    bids: list[tuple[float, float]]  # (price, qty)
    asks: list[tuple[float, float]]

    def mid(self) -> float:
        if not self.bids or not self.asks:
            return float("nan")
        return (self.bids[0][0] + self.asks[0][0]) / 2.0

    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else float("nan")

    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else float("nan")


@dataclass
class TickState:
    tick: int
    books: dict[str, BookSnapshot]
    tenders: list[SyntheticTender] = field(default_factory=list)


def _lognorm_rv(params: dict[str, float], rng: np.random.Generator) -> float:
    return float(
        lognorm.rvs(
            params["shape"],
            loc=params.get("loc", 0.0),
            scale=params["scale"],
            random_state=rng,
        )
    )


def _clip_price(px: float, lo: float, hi: float) -> float:
    return float(np.clip(round(px, 4), lo, hi))


class SyntheticSessionGenerator:
    def __init__(self, model_path: Path | str | None = None) -> None:
        path = Path(model_path) if model_path else DEFAULT_MODEL_REL
        with open(path, encoding="utf-8") as f:
            self.model: dict[str, Any] = json.load(f)
        self.tick_max: int = int(self.model.get("tick_max", 299))
        self.price_bounds: dict[str, tuple[float, float]] = {
            k: tuple(v)  # type: ignore[misc]
            for k, v in self.model.get("price_bounds", {}).items()
        }
        self._tender_id = 0

    def _phase_stats(self, ticker: str, tick_norm: float) -> dict[str, Any]:
        rows = self.model.get("depth_by_phase", {}).get(ticker) or []
        if not rows:
            return {
                "mean_spread_top": 0.05,
                "mean_total_bid": 5000.0,
                "mean_total_ask": 5000.0,
            }
        if tick_norm < 1 / 3:
            key = "early"
        elif tick_norm < 2 / 3:
            key = "mid"
        else:
            key = "late"
        for r in rows:
            if r.get("phase") == key:
                return r
        return rows[0]

    def _build_book(self, ticker: str, tick: int, rng: np.random.Generator) -> BookSnapshot:
        lo, hi = self.price_bounds.get(ticker, (5.0, 20.0))
        tick_norm = tick / max(1.0, float(self.tick_max))
        phase = self._phase_stats(ticker, tick_norm)

        spr_p = self.model["spread_top_lognorm"][ticker]
        spread = max(_lognorm_rv(spr_p, rng), 0.02)
        mid = float(rng.uniform(lo + spread / 2, hi - spread / 2))

        bid1 = mid - spread / 2
        ask1 = mid + spread / 2
        bid1 = _clip_price(bid1, lo, hi)
        ask1 = _clip_price(ask1, lo, hi)
        if ask1 <= bid1:
            ask1 = _clip_price(bid1 + 0.02, lo, hi)

        gap_b = self.model["price_gap_lognorm"][ticker]["bid"]
        gap_a = self.model["price_gap_lognorm"][ticker]["ask"]
        vol_b = self.model["volume_lognorm"][ticker]["bid"]
        vol_a = self.model["volume_lognorm"][ticker]["ask"]

        bid_prices = [bid1]
        for _ in range(BOOK_LEVELS - 1):
            gap = max(_lognorm_rv(gap_b, rng), 0.01)
            nxt = bid_prices[-1] - gap
            if nxt < lo:
                nxt = bid_prices[-1] - 0.01
            bid_prices.append(_clip_price(nxt, lo, hi))

        ask_prices = [ask1]
        for _ in range(BOOK_LEVELS - 1):
            gap = max(_lognorm_rv(gap_a, rng), 0.01)
            nxt = ask_prices[-1] + gap
            if nxt > hi:
                nxt = ask_prices[-1] + 0.01
            ask_prices.append(_clip_price(nxt, lo, hi))

        bids = []
        asks = []
        for i in range(BOOK_LEVELS):
            bq = max(1.0, _lognorm_rv(vol_b, rng))
            aq = max(1.0, _lognorm_rv(vol_a, rng))
            bids.append((bid_prices[i], round(bq, 2)))
            asks.append((ask_prices[i], round(aq, 2)))

        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return BookSnapshot(bids=bids[:BOOK_LEVELS], asks=asks[:BOOK_LEVELS])

    def _maybe_tenders(
        self,
        books: dict[str, BookSnapshot],
        rng: np.random.Generator,
    ) -> list[SyntheticTender]:
        tcfg = self.model["tender"]
        p = float(tcfg.get("prob_tick_empirical", 0.02))
        if rng.random() > p:
            return []

        tickers = [k for k in books if k in self.price_bounds]
        if not tickers:
            return []
        tkr = tickers[int(rng.integers(0, len(tickers)))]
        book = books[tkr]
        mid = book.mid()
        if not np.isfinite(mid):
            return []

        qty_pool = tcfg["quantity_by_ticker"].get(tkr) or [1000.0]
        qty = int(max(1, rng.choice(np.array(qty_pool, dtype=float))))

        buy_frac = float(tcfg.get("action_buy_fraction", 0.5))
        action = "BUY" if rng.random() < buy_frac else "SELL"

        spr_p = (self.model.get("tender_spread_lognorm") or {}).get(tkr)
        if spr_p and isinstance(spr_p, dict) and "shape" in spr_p:
            spread_pct = max(_lognorm_rv(spr_p, rng), 1e-6)
        else:
            spread_pct = 0.01
        if "BUY" in action.upper():
            price = mid * (1.0 - spread_pct)
        else:
            price = mid * (1.0 + spread_pct)
        price = _clip_price(price, *self.price_bounds[tkr])

        self._tender_id += 1
        return [
            SyntheticTender(
                tender_id=self._tender_id,
                ticker=tkr,
                action=action,
                quantity=qty,
                price=price,
            )
        ]

    def iter_session(self, seed: int | None = None) -> Iterator[TickState]:
        rng = np.random.default_rng(seed)
        self._tender_id = 0
        for tick in range(self.tick_max + 1):
            books = {
                tkr: self._build_book(tkr, tick, rng)
                for tkr in self.price_bounds
            }
            tenders = self._maybe_tenders(books, rng)
            yield TickState(tick=tick, books=books, tenders=tenders)

    def generate_session(self, seed: int | None = None) -> list[TickState]:
        return list(self.iter_session(seed=seed))
