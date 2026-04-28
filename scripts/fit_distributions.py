"""
Fit parametric / empirical distributions from aggregated_book_stats.csv and
aggregated_tenders.csv; write data/synthetic_model_params.json for the generator.

Uses scipy MLE for lognormal (positive volumes, price gaps, spreads, tender
spread_pct). Empirical samples (capped) for tender inter-arrival and quantities.

Tender spread_pct per observed row: abs(mid_at_tick - Price) / mid_at_tick
(from aggregated tenders merged with By Tick); fitted lognormal per ticker as
tender_spread_lognorm — the generator sets tender price as mid * (1 ± spread_pct).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as st

BOOK_TICKERS = ("CRZY", "TAME")
PRICE_BOUNDS = {
    "CRZY": (5.01, 19.99),
    "TAME": (15.01, 34.99),
}
EMPIRICAL_CAP = 5000
SCHEMA_VERSION = 1


def _fit_lognorm(positive: np.ndarray) -> dict[str, float] | None:
    x = positive[np.isfinite(positive) & (positive > 0)]
    if len(x) < 30:
        return None
    shape, loc, scale = st.lognorm.fit(x, floc=0)
    if not all(np.isfinite([shape, scale])) or shape <= 0 or scale <= 0:
        return None
    return {"shape": float(shape), "loc": 0.0, "scale": float(scale)}


def _depth_by_phase(book: pd.DataFrame, ticker: str) -> list[dict[str, Any]]:
    sub = book[book["ticker"] == ticker]
    if sub.empty:
        return []
    sub = sub.copy()
    try:
        sub["phase_bin"] = pd.qcut(
            sub["tick_norm"], q=3, labels=["early", "mid", "late"], duplicates="drop"
        )
    except ValueError:
        return []
    out = []
    for ph, g in sub.groupby("phase_bin", observed=True):
        out.append(
            {
                "phase": str(ph),
                "mean_total_bid": float(g["total_bid_vol"].mean()),
                "mean_total_ask": float(g["total_ask_vol"].mean()),
                "mean_spread_top": float(g["spread_top"].mean()),
                "n": int(len(g)),
            }
        )
    return out


def _subsample(arr: np.ndarray, cap: int, rng: np.random.Generator) -> list[float]:
    a = arr[np.isfinite(arr)]
    if len(a) == 0:
        return []
    if len(a) <= cap:
        return [float(x) for x in a]
    idx = rng.choice(len(a), size=cap, replace=False)
    return [float(a[i]) for i in idx]


def fit_models(
    book_path: Path,
    tender_path: Path,
    rng: np.random.Generator,
) -> dict[str, Any]:
    if book_path.exists():
        book = pd.read_csv(book_path)
    else:
        book = pd.DataFrame()

    if not book.empty and "source_file" not in book.columns:
        book["source_file"] = "unknown"
    tenders = pd.read_csv(tender_path) if tender_path.exists() and tender_path.stat().st_size > 0 else pd.DataFrame()

    volume_lognorm: dict[str, Any] = {}
    gap_lognorm: dict[str, Any] = {}
    spread_lognorm: dict[str, Any] = {}
    depth_by_phase: dict[str, list] = {}

    if not book.empty and "ticker" in book.columns:
        for tkr in BOOK_TICKERS:
            sub = book[book["ticker"] == tkr]
            if sub.empty:
                continue

            avg_b = sub.loc[sub["n_bid_levels"] > 0, "avg_bid_vol_per_level"].to_numpy()
            avg_a = sub.loc[sub["n_ask_levels"] > 0, "avg_ask_vol_per_level"].to_numpy()
            bid_gap = sub["bid_price_gap_mean"].to_numpy()
            ask_gap = sub["ask_price_gap_mean"].to_numpy()
            spr = sub["spread_top"].to_numpy()

            vb = _fit_lognorm(avg_b)
            va = _fit_lognorm(avg_a)
            if vb and va:
                volume_lognorm[tkr] = {"bid": vb, "ask": va}

            bg = _fit_lognorm(bid_gap)
            ag = _fit_lognorm(ask_gap)
            if bg and ag:
                gap_lognorm[tkr] = {"bid": bg, "ask": ag}

            sp = _fit_lognorm(spr)
            if sp:
                spread_lognorm[tkr] = sp

            depth_by_phase[tkr] = _depth_by_phase(book, tkr)

    tender_block: dict[str, Any] = {
        "interarrival_ticks": [],
        "quantity_by_ticker": {t: [] for t in BOOK_TICKERS},
        "action_buy_fraction": 0.5,
    }

    tender_spread_lognorm: dict[str, Any] = {}

    if not tenders.empty and "source_file" in tenders.columns and "Tick" in tenders.columns:
        inter_list: list[int] = []
        for _, g in tenders.groupby("source_file"):
            ticks = sorted(g["Tick"].astype(int).unique().tolist())
            if len(ticks) < 2:
                continue
            inter_list.extend(int(ticks[i + 1] - ticks[i]) for i in range(len(ticks) - 1))
        if inter_list:
            arr = np.array(inter_list, dtype=float)
            tender_block["interarrival_ticks"] = _subsample(arr, EMPIRICAL_CAP, rng)

        if "Action" in tenders.columns:
            s = tenders["Action"].astype(str).str.upper()
            buy_frac = float(s.str.contains("BUY").mean()) if len(s) else 0.5
            tender_block["action_buy_fraction"] = max(0.05, min(0.95, buy_frac))

        for tkr in BOOK_TICKERS:
            tg = (
                tenders[tenders["Ticker"].astype(str) == tkr]
                if "Ticker" in tenders.columns
                else tenders.iloc[0:0]
            )
            if tg.empty:
                continue
            if "Quantity" in tg.columns:
                tender_block["quantity_by_ticker"][tkr] = _subsample(
                    tg["Quantity"].to_numpy(dtype=float), EMPIRICAL_CAP, rng
                )

            if (
                "Price" in tg.columns
                and "mid_at_tick" in tg.columns
                and not tg.empty
            ):
                mid = tg["mid_at_tick"].to_numpy(dtype=float)
                px = tg["Price"].to_numpy(dtype=float)
                ok = np.isfinite(mid) & np.isfinite(px) & (mid > 0)
                spread_pct = np.abs(mid[ok] - px[ok]) / mid[ok]
                fit_sp = _fit_lognorm(spread_pct)
                if fit_sp:
                    tender_spread_lognorm[tkr] = fit_sp

        n_sess = int(book["source_file"].nunique()) if not book.empty else 0
        if n_sess <= 0:
            n_sess = int(tenders["source_file"].nunique()) if "source_file" in tenders.columns else 1
        ticks_with = int(tenders.groupby("source_file")["Tick"].nunique().sum())
        tender_block["prob_tick_empirical"] = min(
            0.99, max(0.001, ticks_with / (n_sess * 300.0))
        )
    else:
        tender_block["prob_tick_empirical"] = 0.02

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tick_max": 299,
        "price_bounds": {k: list(v) for k, v in PRICE_BOUNDS.items()},
        "sample_counts": {"book_rows": int(len(book)), "tender_rows": int(len(tenders))},
        "volume_lognorm": volume_lognorm,
        "price_gap_lognorm": gap_lognorm,
        "spread_top_lognorm": spread_lognorm,
        "depth_by_phase": depth_by_phase,
        "tender_spread_lognorm": tender_spread_lognorm,
        "tender": tender_block,
    }


def add_defaults(model: dict[str, Any], rng: np.random.Generator) -> None:
    """Fill missing fits with conservative defaults so the generator always runs."""
    for tkr in BOOK_TICKERS:
        if tkr not in model["volume_lognorm"]:
            model["volume_lognorm"][tkr] = {
                "bid": {"shape": 1.0, "loc": 0.0, "scale": 500.0},
                "ask": {"shape": 1.0, "loc": 0.0, "scale": 500.0},
            }
        if tkr not in model["price_gap_lognorm"]:
            model["price_gap_lognorm"][tkr] = {
                "bid": {"shape": 0.5, "loc": 0.0, "scale": 0.02},
                "ask": {"shape": 0.5, "loc": 0.0, "scale": 0.02},
            }
        if tkr not in model["spread_top_lognorm"]:
            model["spread_top_lognorm"][tkr] = {"shape": 0.8, "loc": 0.0, "scale": 0.05}
        if not model["depth_by_phase"].get(tkr):
            model["depth_by_phase"][tkr] = [
                {"phase": "early", "mean_total_bid": 5e3, "mean_total_ask": 5e3, "mean_spread_top": 0.05, "n": 1},
                {"phase": "mid", "mean_total_bid": 5e3, "mean_total_ask": 5e3, "mean_spread_top": 0.05, "n": 1},
                {"phase": "late", "mean_total_bid": 5e3, "mean_total_ask": 5e3, "mean_spread_top": 0.05, "n": 1},
            ]
        for side in ("bid", "ask"):
            if side not in model["volume_lognorm"][tkr]:
                model["volume_lognorm"][tkr][side] = {"shape": 1.0, "loc": 0.0, "scale": 500.0}
            if side not in model["price_gap_lognorm"][tkr]:
                model["price_gap_lognorm"][tkr][side] = {"shape": 0.5, "loc": 0.0, "scale": 0.02}

    ts = model.setdefault("tender_spread_lognorm", {})
    for tkr in BOOK_TICKERS:
        cur = ts.get(tkr)
        if cur is None or not isinstance(cur, dict) or "shape" not in cur:
            ts[tkr] = {"shape": 0.6, "loc": 0.0, "scale": 0.015}

    t = model["tender"]
    if not t.get("interarrival_ticks"):
        t["interarrival_ticks"] = [5, 10, 20, 30, 50]
    for tkr in BOOK_TICKERS:
        if not t["quantity_by_ticker"].get(tkr):
            t["quantity_by_ticker"][tkr] = [1000.0, 2500.0, 5000.0, 10_000.0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--book-csv",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "aggregated_book_stats.csv",
    )
    ap.add_argument(
        "--tender-csv",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "aggregated_tenders.csv",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "synthetic_model_params.json",
    )
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.book_csv.exists():
        print(f"Note: {args.book_csv} not found — fits will use defaults only.")

    rng = np.random.default_rng(args.seed)
    model = fit_models(args.book_csv, args.tender_csv, rng)
    add_defaults(model, rng)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2)
    print(f"Wrote {args.output} (book_rows={model['sample_counts']['book_rows']})")


if __name__ == "__main__":
    main()
