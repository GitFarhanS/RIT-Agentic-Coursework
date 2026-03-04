"""
Log each tick of the RIT simulation to an xlsx file:
- For each tick: full order book (bids and asks) for each stock, plus best bid/ask summary.
- For each tick: whether there is a tender and its price (tenders are NOT accepted).
One xlsx file is produced per simulation run.
"""

import time
from pathlib import Path
from datetime import datetime

from sdk import RotmanSDK

# --- Config ---
API_KEY = "XXXXXXXX"
HOST = "http://192.168.64.9:9999/v1"
POLL_INTERVAL_SEC = 1.0
OUTPUT_DIR = Path(__file__).resolve().parent / "simulation_logs"
BOOK_DEPTH = 20  # max bid/ask levels to store per side (matches API limit)


def get_security_bid_ask(sec: dict) -> tuple[str, float, float]:
    """Return (ticker, best_bid, best_ask) from a security dict from the API."""
    ticker = sec.get("ticker", "")
    bid = float(sec.get("bid") or sec.get("bidPrice") or 0)
    ask = float(sec.get("ask") or sec.get("askPrice") or 0)
    return ticker, bid, ask


def collect_tick_data(client: RotmanSDK) -> tuple[int, list[dict], list[dict]]:
    """
    Get current tick, list of securities with best bid/ask and full order book, and list of tenders.
    Returns (tick, list of {ticker, bid, ask, bids: [(price,qty),...], asks: [(price,qty),...]}, list of tender dicts).
    """
    case = client.get_case()
    tick = case.get("tick", 0)
    status = case.get("status", "")

    securities_raw = client.get_securities()
    if not isinstance(securities_raw, list):
        securities_raw = [securities_raw] if securities_raw else []

    stocks = []
    for sec in securities_raw:
        ticker, bid, ask = get_security_bid_ask(sec)
        if not ticker:
            continue
        bids_list = []
        asks_list = []
        try:
            book = client.get_book(ticker, limit=BOOK_DEPTH)
            bids_list = [(lev.price, getattr(lev, "quantity_remaining", 0)) for lev in book.bids[:BOOK_DEPTH]]
            asks_list = [(lev.price, getattr(lev, "quantity_remaining", 0)) for lev in book.asks[:BOOK_DEPTH]]
        except Exception as e:
            print(f"  get_book({ticker}) failed: {e}")
        stocks.append({
            "ticker": ticker,
            "bid": bid,
            "ask": ask,
            "bids": bids_list,
            "asks": asks_list,
        })

    tenders_raw = client.get_tenders()
    tenders = []
    if tenders_raw:
        for t in tenders_raw:
            action = getattr(t, "action", None)
            tenders.append({
                "tender_id": getattr(t, "id", ""),
                "ticker": getattr(t, "ticker", ""),
                "action": str(action) if action is not None else "",
                "quantity": getattr(t, "quantity", 0),
                "price": getattr(t, "price", 0),
                "tick": getattr(t, "tick", 0),
                "expires": getattr(t, "expiry_tick", 0),
            })
    return tick, stocks, tenders


def run_simulation_logger():
    client = RotmanSDK(API_KEY=API_KEY, HOST=HOST)
    case = client.get_case()
    print(f"Case: {case.get('name', 'unknown')}, status: {case.get('status')}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"simulation_run_{run_id}.xlsx"

    # Collect tick snapshots: list of { tick, stocks: [{ticker,bid,ask}], tenders: [...] }
    snapshots = []
    seen_tick = set()
    last_tick = -1

    try:
        while True:
            tick, stocks, tenders = collect_tick_data(client)
            case = client.get_case()
            status = case.get("status", "ACTIVE")

            if status in ("STOPPED", "PAUSED") and tick == 0:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            if tick not in seen_tick:
                seen_tick.add(tick)
                snapshots.append({
                    "tick": tick,
                    "stocks": stocks,
                    "tenders": tenders,
                })
                print(f"  tick {tick}: {len(stocks)} securities, {len(tenders)} tender(s) (not accepted)")

            if status == "STOPPED" or tick >= 299:
                print("Simulation ended.")
                break

            last_tick = tick
            time.sleep(POLL_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("Interrupted.")

    if not snapshots:
        print("No tick data collected; not writing xlsx.")
        return

    # Build column set: tick, then for each stock ticker: {ticker}_bid, {ticker}_ask, then tender columns
    all_tickers = []
    for s in snapshots:
        for st in s["stocks"]:
            t = st["ticker"]
            if t and t not in all_tickers:
                all_tickers.append(t)

    wb = Workbook()
    ws = wb.active
    ws.title = "By tick"

    # Header row
    headers = ["tick"]
    for t in all_tickers:
        headers.append(f"{t}_bid")
        headers.append(f"{t}_ask")
    headers.extend([
        "has_tender",
        "tender_price",
        "tender_ticker",
        "tender_action",
        "tender_quantity",
        "tender_expires",
    ])
    ws.append(headers)

    for snap in snapshots:
        row = [snap["tick"]]
        ticker_to_bid_ask = {st["ticker"]: (st["bid"], st["ask"]) for st in snap["stocks"]}
        for t in all_tickers:
            bid, ask = ticker_to_bid_ask.get(t, (None, None))
            row.append(bid if bid is not None else "")
            row.append(ask if ask is not None else "")

        tenders = snap["tenders"]
        if tenders:
            # First tender only in main sheet
            t0 = tenders[0]
            row.append("Y")
            row.append(t0.get("price", ""))
            row.append(t0.get("ticker", ""))
            row.append(str(t0.get("action", "")))
            row.append(t0.get("quantity", ""))
            row.append(t0.get("expires", ""))
        else:
            row.extend(["N", "", "", "", "", ""])
        ws.append(row)

    # Per-ticker order book sheets: tick, then bid_1_price, bid_1_qty, ... ask_1_price, ask_1_qty, ...
    book_headers = ["tick"]
    for i in range(1, BOOK_DEPTH + 1):
        book_headers.append(f"bid_{i}_price")
        book_headers.append(f"bid_{i}_qty")
    for i in range(1, BOOK_DEPTH + 1):
        book_headers.append(f"ask_{i}_price")
        book_headers.append(f"ask_{i}_qty")

    ticker_to_stocks = {}  # tick -> { ticker -> {bid, ask, bids, asks} }
    for snap in snapshots:
        ticker_to_stocks[snap["tick"]] = {st["ticker"]: st for st in snap["stocks"]}

    for ticker in all_tickers:
        sheet_name = f"Book_{ticker}"[:31]  # Excel sheet name max 31 chars
        ws_book = wb.create_sheet(sheet_name)
        ws_book.append(book_headers)
        for snap in snapshots:
            row = [snap["tick"]]
            st = ticker_to_stocks.get(snap["tick"], {}).get(ticker)
            bids = (st.get("bids") or [])[:BOOK_DEPTH] if st else []
            asks = (st.get("asks") or [])[:BOOK_DEPTH] if st else []
            for i in range(BOOK_DEPTH):
                row.extend([bids[i][0], bids[i][1]] if i < len(bids) else ["", ""])
            for i in range(BOOK_DEPTH):
                row.extend([asks[i][0], asks[i][1]] if i < len(asks) else ["", ""])
            ws_book.append(row)

    # Optional: sheet with one row per tender (all tenders across ticks)
    if any(s["tenders"] for s in snapshots):
        ws_t = wb.create_sheet("Tenders")
        ws_t.append(["tick", "tender_id", "ticker", "action", "quantity", "price", "expires"])
        for snap in snapshots:
            for t in snap["tenders"]:
                ws_t.append([
                    snap["tick"],
                    t.get("tender_id"),
                    t.get("ticker"),
                    t.get("action"),
                    t.get("quantity"),
                    t.get("price"),
                    t.get("expires"),
                ])

    wb.save(out_path)
    print(f"Saved: {out_path}")
    wb.close()


if __name__ == "__main__":
    run_simulation_logger()
