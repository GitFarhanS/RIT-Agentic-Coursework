"""
Offline fitness for GA / search: expected session P&L under a threshold tender rule
on synthetic books (same edge definition as agents/reactive_agent.py).

Unwind P&L uses two tranches (best vs one level worse), slice-limited fillable size,
and a residual inventory penalty scaled by time_decay_factor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import json
import time

from synthetic.generator import BookSnapshot, SyntheticSessionGenerator, SyntheticTender

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "data" / "synthetic_model_params.json"

# Penalty per share for quantity that cannot be unwound within visible depth * slice_size_ratio.
RESIDUAL_PENALTY_RATE = 0.01

_DEFAULT_FITNESS_PARAMS: dict[str, float] = {
    "threshold": 0.004,
    "market_order_ratio": 1.0,
    "time_decay_factor": 1.0,
    "slice_size_ratio": 1.0,
}

# Clip evolved reals to GA / model bounds (threshold is not clipped so e.g. 0.0 stays valid for tests).
_CLIP_BOUNDS: dict[str, tuple[float, float]] = {
    "market_order_ratio": (0.0, 1.0),
    "time_decay_factor": (0.5, 3.0),
    "slice_size_ratio": (0.2, 1.0),
}


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "8cbd08",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            Path(__file__).resolve().parent.parent / ".cursor" / "debug-8cbd08.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion


def _merged_params(params: dict[str, Any] | None) -> dict[str, float]:
    """Merge caller params with defaults and clip evolved reals to valid ranges."""
    out = dict(_DEFAULT_FITNESS_PARAMS)
    if params:
        for k in _DEFAULT_FITNESS_PARAMS:
            if k in params:
                out[k] = float(params[k])
    for k, (lo, hi) in _CLIP_BOUNDS.items():
        out[k] = float(np.clip(out[k], lo, hi))
    return out


def _edge(mid: float, tender_price: float, action: str) -> float:
    if mid <= 0 or not np.isfinite(mid):
        return 0.0
    if "BUY" in action.upper():
        return (mid - tender_price) / mid
    return (tender_price - mid) / mid


def _visible_bid_volume(book: BookSnapshot) -> float:
    return float(sum(q for _, q in book.bids)) if book.bids else 0.0


def _visible_ask_volume(book: BookSnapshot) -> float:
    return float(sum(q for _, q in book.asks)) if book.asks else 0.0


def _accept_pnl(
    book: BookSnapshot,
    t: SyntheticTender,
    market_order_ratio: float,
    slice_size_ratio: float,
    time_decay_factor: float,
) -> float:
    """
    P&L from unwinding an accepted tender: two tranches at bid/ask prices,
    minus residual penalty for quantity beyond visible_volume * slice_size_ratio.
    """
    is_buy = "BUY" in t.action.upper()
    qty = float(t.quantity)
    mor = float(market_order_ratio)
    ssr = float(slice_size_ratio)
    tdf = float(time_decay_factor)

    if is_buy:
        visible = _visible_bid_volume(book)
    else:
        visible = _visible_ask_volume(book)

    cap = visible * ssr
    residual = max(0.0, qty - cap)
    unwind_qty = qty - residual

    if unwind_qty <= 0.0:
        tranche_pnl = 0.0
    else:
        q_mo = unwind_qty * mor
        q_lv = unwind_qty * (1.0 - mor)

        if is_buy:
            p0 = book.bids[0][0]
            p1 = book.bids[1][0] if len(book.bids) > 1 else book.bids[-1][0]
            tranche_pnl = (p0 - float(t.price)) * q_mo + (p1 - float(t.price)) * q_lv
        else:
            p0 = book.asks[0][0]
            p1 = book.asks[1][0] if len(book.asks) > 1 else book.asks[-1][0]
            tranche_pnl = (float(t.price) - p0) * q_mo + (float(t.price) - p1) * q_lv

    residual_penalty = residual * RESIDUAL_PENALTY_RATE * tdf
    return float(tranche_pnl - residual_penalty)


def _would_accept(book: BookSnapshot, t: SyntheticTender, threshold: float) -> bool:
    if not book.bids or not book.asks:
        return False
    return _edge(book.mid(), t.price, t.action) > threshold


def score_tender(book: BookSnapshot, t: SyntheticTender, params: dict[str, Any] | None) -> float:
    """
    P&L contribution of one tender against one book (0 if declined or missing depth).
    Accept iff edge > threshold (uses params['threshold'] merged with defaults).
    """
    p = _merged_params(params)
    if not _would_accept(book, t, p["threshold"]):
        return 0.0
    return _accept_pnl(
        book,
        t,
        p["market_order_ratio"],
        p["slice_size_ratio"],
        p["time_decay_factor"],
    )


def count_accepts(
    params: dict[str, Any] | None = None,
    *,
    model_path: Path | str | None = None,
    n_sessions: int = 10,
    base_seed: int = 0,
) -> int:
    """Total accept count over all synthetic sessions (same rule as evaluate)."""
    p = _merged_params(params)
    threshold = p["threshold"]
    gen = SyntheticSessionGenerator(model_path or DEFAULT_MODEL)
    total = 0
    for si in range(n_sessions):
        for state in gen.iter_session(seed=base_seed + si):
            for t in state.tenders:
                book = state.books.get(t.ticker)
                if not book:
                    continue
                if _would_accept(book, t, threshold):
                    total += 1
    return total


def evaluate(
    params: dict[str, Any] | None = None,
    *,
    model_path: Path | str | None = None,
    n_sessions: int = 10,
    base_seed: int = 0,
) -> float:
    """
    Return mean realised-style P&L per synthetic session for the given parameters.

    params keys: threshold, market_order_ratio, time_decay_factor, slice_size_ratio
    (missing keys use defaults; values are clipped to valid ranges).
    """
    merged = _merged_params(params)
    _debug_log(
        "H1-H4",
        "ga/fitness.py:evaluate:entry",
        "evaluate start",
        {
            "n_sessions": n_sessions,
            "base_seed": base_seed,
            "params": merged,
            "model_path": str(model_path or DEFAULT_MODEL),
        },
    )
    gen = SyntheticSessionGenerator(model_path or DEFAULT_MODEL)
    session_pnls: list[float] = []
    for si in range(n_sessions):
        pnl_sess = 0.0
        tender_count = 0
        buy_count = 0
        sell_count = 0
        accept_count = 0
        accept_buy = 0
        accept_sell = 0
        edge_buy: list[float] = []
        edge_sell: list[float] = []
        accepted_pnls: list[float] = []
        for state in gen.iter_session(seed=base_seed + si):
            for t in state.tenders:
                book = state.books.get(t.ticker)
                if not book:
                    continue
                tender_count += 1
                is_buy = "BUY" in t.action.upper()
                if is_buy:
                    buy_count += 1
                else:
                    sell_count += 1
                edge = _edge(book.mid(), t.price, t.action)
                if is_buy:
                    edge_buy.append(edge)
                else:
                    edge_sell.append(edge)
                accepted = _would_accept(book, t, merged["threshold"])
                if accepted:
                    accept_count += 1
                    if is_buy:
                        accept_buy += 1
                    else:
                        accept_sell += 1
                    pnl = _accept_pnl(
                        book,
                        t,
                        merged["market_order_ratio"],
                        merged["slice_size_ratio"],
                        merged["time_decay_factor"],
                    )
                    accepted_pnls.append(float(pnl))
                    pnl_sess += float(pnl)
        session_pnls.append(pnl_sess)
        _debug_log(
            "H1-H4",
            "ga/fitness.py:evaluate:session",
            "session stats",
            {
                "session_index": si,
                "seed": base_seed + si,
                "tender_count": tender_count,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "accept_count": accept_count,
                "accept_buy": accept_buy,
                "accept_sell": accept_sell,
                "mean_edge_buy": float(np.mean(edge_buy)) if edge_buy else None,
                "mean_edge_sell": float(np.mean(edge_sell)) if edge_sell else None,
                "max_edge_buy": float(np.max(edge_buy)) if edge_buy else None,
                "max_edge_sell": float(np.max(edge_sell)) if edge_sell else None,
                "mean_accepted_pnl": float(np.mean(accepted_pnls)) if accepted_pnls else None,
                "session_pnl": float(pnl_sess),
            },
        )
    if not session_pnls:
        _debug_log(
            "H1",
            "ga/fitness.py:evaluate:exit",
            "no sessions produced",
            {"result": 0.0},
        )
        return 0.0
    result = float(np.mean(session_pnls))
    _debug_log(
        "H1-H4",
        "ga/fitness.py:evaluate:exit",
        "evaluate done",
        {"mean_session_pnl": result},
    )
    return result
