"""
Aggregate RIT simulation logger xlsx files into CSVs for offline model fitting.

Input layout matches logging/simulation_logger.py: sheets "By Tick", "Book {TICKER}",
"Tenders". Files without any "Book *" sheet are skipped (e.g. reactive_agent-only logs).

Limitations (for reports): "By Tick" logs only the first tender per tick; books may repeat
when best bid/ask are unchanged between logger polls; this aggregation does not reconstruct
full market intent beyond logged depth.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

BOOK_DEPTH = 20
SESSION_RE = re.compile(r"simulation_run_s(\d+)_", re.I)


def _parse_session_index(filename: str) -> int | None:
    m = SESSION_RE.search(filename)
    return int(m.group(1)) if m else None


def _book_sheet_names(sheet_names: list[str]) -> list[str]:
    return [n for n in sheet_names if n.startswith("Book ")]


def _row_book_metrics(row: pd.Series, ticker: str) -> dict:
    bid_qty = [row.get(f"Bid {i} Qty") for i in range(1, BOOK_DEPTH + 1)]
    ask_qty = [row.get(f"Ask {i} Qty") for i in range(1, BOOK_DEPTH + 1)]
    bid_px = [row.get(f"Bid {i} Price") for i in range(1, BOOK_DEPTH + 1)]
    ask_px = [row.get(f"Ask {i} Price") for i in range(1, BOOK_DEPTH + 1)]

    bq = pd.Series(bid_qty, dtype="float64")
    aq = pd.Series(ask_qty, dtype="float64")
    bp = pd.Series(bid_px, dtype="float64")
    ap = pd.Series(ask_px, dtype="float64")

    n_bid = int((bq.notna() & (bq > 0)).sum())
    n_ask = int((aq.notna() & (aq > 0)).sum())

    total_bid = float(bq.fillna(0).clip(lower=0).sum())
    total_ask = float(aq.fillna(0).clip(lower=0).sum())

    avg_bid_lvl = float(bq[bq > 0].mean()) if n_bid else np.nan
    avg_ask_lvl = float(aq[aq > 0].mean()) if n_ask else np.nan

    bp_valid = bp.dropna()
    ap_valid = ap.dropna()
    bid_gap = float(bp_valid.diff().abs().mean()) if len(bp_valid) > 1 else np.nan
    ask_gap = float(ap_valid.diff().abs().mean()) if len(ap_valid) > 1 else np.nan

    bb = row.get("Bid 1 Price")
    ba = row.get("Ask 1 Price")
    spread_top = np.nan
    if pd.notna(bb) and pd.notna(ba):
        spread_top = float(ba) - float(bb)

    tick = int(row["Tick"]) if pd.notna(row["Tick"]) else 0

    return {
        "ticker": ticker,
        "tick": tick,
        "total_bid_vol": total_bid,
        "total_ask_vol": total_ask,
        "n_bid_levels": n_bid,
        "n_ask_levels": n_ask,
        "avg_bid_vol_per_level": avg_bid_lvl,
        "avg_ask_vol_per_level": avg_ask_lvl,
        "bid_price_gap_mean": bid_gap,
        "ask_price_gap_mean": ask_gap,
        "best_bid": float(bb) if pd.notna(bb) else np.nan,
        "best_ask": float(ba) if pd.notna(ba) else np.nan,
        "spread_top": spread_top,
    }


def _enrich_tenders_with_by_tick(
    tenders: pd.DataFrame, by_tick: pd.DataFrame | None, source_file: str
) -> pd.DataFrame:
    if by_tick is None or by_tick.empty or tenders.empty:
        t = tenders.copy()
        t["source_file"] = source_file
        t["mid_at_tick"] = np.nan
        t["spread_top_at_tick"] = np.nan
        return t

    bt = by_tick.copy()
    bt["source_file"] = source_file
    tickers_in_bt = [c.replace(" Bid", "") for c in bt.columns if c.endswith(" Bid")]

    rows = []
    for _, tr in tenders.iterrows():
        tick = tr["Tick"]
        tick_row = bt.loc[bt["Tick"] == tick]
        tkr = str(tr.get("Ticker", "") or "")
        mid = np.nan
        sp = np.nan
        if not tick_row.empty and tkr and tkr in tickers_in_bt:
            r0 = tick_row.iloc[0]
            bid = r0.get(f"{tkr} Bid")
            ask = r0.get(f"{tkr} Ask")
            if pd.notna(bid) and pd.notna(ask) and bid != "" and ask != "":
                bidf, askf = float(bid), float(ask)
                mid = (bidf + askf) / 2
                sp = askf - bidf
        row = dict(tr)
        row["source_file"] = source_file
        row["mid_at_tick"] = mid
        row["spread_top_at_tick"] = sp
        rows.append(row)

    return pd.DataFrame(rows)


def aggregate_file(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    names = list(xl.keys())
    book_names = _book_sheet_names(names)
    if not book_names:
        return pd.DataFrame(), pd.DataFrame()

    source_file = path.name
    session_index = _parse_session_index(path.name)

    by_tick = xl.get("By Tick")
    if by_tick is not None and not isinstance(by_tick, pd.DataFrame):
        by_tick = None

    metrics: list[dict] = []
    for bname in book_names:
        ticker = bname.replace("Book ", "").strip()
        df = xl[bname]
        if df.empty or "Tick" not in df.columns:
            continue
        for _, row in df.iterrows():
            m = _row_book_metrics(row, ticker)
            m["source_file"] = source_file
            m["session_index"] = session_index
            m["tick_norm"] = float(m["tick"]) / 299.0
            metrics.append(m)

    book_df = pd.DataFrame(metrics)

    tenders_raw = xl.get("Tenders")
    if tenders_raw is None or not isinstance(tenders_raw, pd.DataFrame) or tenders_raw.empty:
        tenders_out = pd.DataFrame(
            columns=[
                "Tick",
                "Tender ID",
                "Ticker",
                "Action",
                "Quantity",
                "Price",
                "Expires",
                "source_file",
                "mid_at_tick",
                "spread_top_at_tick",
            ]
        )
        return book_df, tenders_out

    tdf = tenders_raw.drop_duplicates(subset=["Tender ID"], keep="first")
    tenders_out = _enrich_tenders_with_by_tick(tdf, by_tick, source_file)
    return book_df, tenders_out


def main() -> None:
    p = argparse.ArgumentParser(description="Aggregate simulation logger xlsx into CSVs.")
    p.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "logging" / "simulation_logs",
        help="Directory containing *.xlsx simulation logs",
    )
    p.add_argument(
        "--output-book",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "aggregated_book_stats.csv",
    )
    p.add_argument(
        "--output-tenders",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "aggregated_tenders.csv",
    )
    args = p.parse_args()

    files = sorted(args.input_dir.glob("*.xlsx"))
    if not files:
        print(f"No xlsx files in {args.input_dir}")
        return

    book_parts: list[pd.DataFrame] = []
    tender_parts: list[pd.DataFrame] = []
    processed = 0
    for f in files:
        b, t = aggregate_file(f)
        if b.empty:
            continue
        processed += 1
        book_parts.append(b)
        if not t.empty:
            tender_parts.append(t)

    args.output_book.parent.mkdir(parents=True, exist_ok=True)

    if not book_parts:
        print("No book data aggregated (no valid simulation logs).")
        return

    book_all = pd.concat(book_parts, ignore_index=True)
    book_all.to_csv(args.output_book, index=False)
    print(f"Wrote {len(book_all)} book rows -> {args.output_book}")

    if tender_parts:
        tender_all = pd.concat(tender_parts, ignore_index=True)
        tender_all.to_csv(args.output_tenders, index=False)
        print(f"Wrote {len(tender_all)} tender rows -> {args.output_tenders}")
    else:
        pd.DataFrame().to_csv(args.output_tenders, index=False)
        print(f"No tenders; wrote empty {args.output_tenders}")

    print(f"Files with book data: {processed} / {len(files)}")


if __name__ == "__main__":
    main()
