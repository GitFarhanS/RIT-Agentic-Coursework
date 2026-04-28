#!/usr/bin/env python3
"""
Standalone fitness contract checks for ga/fitness.py.
Run from project root: python validate_fitness.py
Exit 0 if all pass, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ga.fitness import (  # noqa: E402
    DEFAULT_MODEL,
    _accept_pnl,
    count_accepts,
    evaluate,
    score_tender,
)
from synthetic.generator import BookSnapshot, SyntheticTender  # noqa: E402

# Explicit evolved params for checks that need full dict / _accept_pnl tranche behaviour.
_FULL = {
    "threshold": 0.01,
    "market_order_ratio": 1.0,
    "time_decay_factor": 1.0,
    "slice_size_ratio": 1.0,
}


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    model_path = DEFAULT_MODEL

    # 1. Determinism
    reason = ""
    ok = True
    if not model_path.is_file():
        ok = False
        reason = f"missing model file {model_path}"
    else:
        params = {
            "threshold": 0.02,
            "market_order_ratio": 0.5,
            "time_decay_factor": 1.0,
            "slice_size_ratio": 0.8,
        }
        a = evaluate(params, model_path=model_path, n_sessions=3, base_seed=7)
        b = evaluate(params, model_path=model_path, n_sessions=3, base_seed=7)
        if a != b:
            ok = False
            reason = f"got {a!r} vs {b!r}"
        elif a != a:  # NaN
            ok = False
            reason = "non-finite fitness"
    results.append(("1. Determinism", ok, reason))

    # 2. Threshold gate (edge == threshold -> no accept)
    mid_target = 100.01
    # Bid 100, ask 100.02 -> mid 100.01; tender price so (mid - p)/mid == 0.05 exactly
    thr = 0.05
    tender_px = mid_target * (1.0 - thr)
    book = BookSnapshot(
        bids=[(100.0, 1_000.0)],
        asks=[(100.02, 1_000.0)],
    )
    t = SyntheticTender(1, "CRZY", "BUY", 100, tender_px)
    got = score_tender(book, t, {**_FULL, "threshold": thr})
    ok2 = got == 0.0
    r2 = "" if ok2 else f"expected 0 contribution, got {got!r}"
    results.append(("2. Threshold gate", ok2, r2))

    # 3. BUY sign (price above best bid -> negative _accept_pnl)
    book_buy = BookSnapshot(bids=[(10.0, 100.0)], asks=[(10.5, 100.0)])
    t_buy = SyntheticTender(2, "CRZY", "BUY", 1, 11.0)
    pnl_buy = _accept_pnl(book_buy, t_buy, 1.0, 1.0, 1.0)
    ok3 = pnl_buy < 0
    r3 = "" if ok3 else f"_accept_pnl={pnl_buy}, expected < 0"
    results.append(("3. BUY sign (_accept_pnl)", ok3, r3))

    # 4. SELL sign (price below best ask -> negative _accept_pnl)
    book_sell = BookSnapshot(bids=[(9.5, 100.0)], asks=[(10.5, 100.0)])
    t_sell = SyntheticTender(3, "CRZY", "SELL", 1, 10.0)
    pnl_sell = _accept_pnl(book_sell, t_sell, 1.0, 1.0, 1.0)
    ok4 = pnl_sell < 0
    r4 = "" if ok4 else f"_accept_pnl={pnl_sell}, expected < 0"
    results.append(("4. SELL sign (_accept_pnl)", ok4, r4))

    # 5. Monotonicity of accept count vs increasing threshold
    ok5 = True
    r5 = ""
    if not model_path.is_file():
        ok5 = False
        r5 = "missing model file"
    else:
        n_sess, seed = 4, 123
        c0 = count_accepts(dict(_FULL, threshold=0.0), model_path=model_path, n_sessions=n_sess, base_seed=seed)
        c1 = count_accepts(dict(_FULL, threshold=0.02), model_path=model_path, n_sessions=n_sess, base_seed=seed)
        c2 = count_accepts(dict(_FULL, threshold=0.15), model_path=model_path, n_sessions=n_sess, base_seed=seed)
        if not (c2 <= c1 <= c0):
            ok5 = False
            r5 = f"accept counts not monotone non-increasing: {c0=}, {c1=}, {c2=}"
    results.append(("5. Accept-count monotonicity", ok5, r5))

    # 6. Edge cases — empty book / missing depth -> 0 contribution; no crash
    ok6 = True
    r6 = ""
    try:
        empty = BookSnapshot(bids=[], asks=[])
        t_any = SyntheticTender(4, "CRZY", "BUY", 1000, 1.0)
        if score_tender(empty, t_any, _FULL) != 0.0:
            ok6 = False
            r6 = "empty book should score 0"
        partial = BookSnapshot(bids=[(10.0, 100.0)], asks=[])
        if score_tender(partial, t_any, _FULL) != 0.0:
            ok6 = False
            r6 = r6 or "missing asks should score 0"
    except Exception as e:  # noqa: BLE001
        ok6 = False
        r6 = str(e)
    results.append(("6. Edge cases (empty / partial book)", ok6, r6))

    passed = 0
    for name, ok_i, why in results:
        if ok_i:
            print(f"PASS — {name}")
            passed += 1
        else:
            print(f"FAIL — {name}: {why}")

    print(f"\n{passed}/6 checks passed")
    return 0 if passed == 6 else 1


if __name__ == "__main__":
    sys.exit(main())
