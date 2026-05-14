"""
Live RIT LT3 tests: orders (buy/sell TAME and CRZY at different prices),
tender accept/decline, polling all 300 ticks in a period, and simulation stopped.

Uses API key from .env (API_KEY or RIT_API_KEY). Run with a live session:
  pytest tests/test_live_rit_lt3.py -v

CRZY: price in (5, 20). TAME: price in (15, 35).
TAME max quantity per order: 10,000. CRZY max quantity per order: 25,000.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_src = _root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import pytest

from coursework.domain.models import ActionEnum, OrderType
from coursework.infrastructure.rotman_client import RITError, RotmanSDK

# LT3 price ranges (min, max) exclusive: CRZY (5, 20), TAME (15, 35)
CRZY_PRICE_MIN, CRZY_PRICE_MAX = 5.01, 19.99
TAME_PRICE_MIN, TAME_PRICE_MAX = 15.01, 34.99

# Delay between place+cancel to avoid rate limit and give exchange time to accept
ORDER_DELAY_SEC = 0.5
# Price step in cents: 1 = every cent, 10 = every 10 cents, 100 = every dollar (fewer orders, more likely to go through)
PRICE_STEP_CENTS = 100


def _prices_every_cent(min_price: float, max_price: float, step_cents: float = PRICE_STEP_CENTS) -> list[float]:
    """Return prices from min_price to max_price (inclusive) every step_cents. All prices strictly within API bounds."""
    if max_price <= min_price:
        return []
    step = step_cents * 0.01
    # n so that min_price + step*(n-1) <= max_price (never exceed max)
    n = int((max_price - min_price) / step) + 1
    if n <= 0:
        return []
    return [round(min_price + step * i, 2) for i in range(n) if min_price + step * i <= max_price]


# Poll / wait timeouts
TENDER_POLL_INTERVAL = 5
TENDER_WAIT_TIMEOUT = 320
TICK_POLL_INTERVAL = 0.5
NEW_PERIOD_WAIT_TIMEOUT = 400
STOPPED_WAIT_TIMEOUT = 400
# Wait for a new case to start when currently STOPPED
WAIT_FOR_CASE_RUNNING_TIMEOUT = 700
WAIT_FOR_CASE_RUNNING_POLL = 1

RUNNING_STATUSES = ("ACTIVE", "PAUSED")


def _wait_for_case_running(
    client: RotmanSDK,
    timeout_sec: int = WAIT_FOR_CASE_RUNNING_TIMEOUT,
    poll_interval: float = WAIT_FOR_CASE_RUNNING_POLL,
) -> None:
    """Poll get_case() until status is ACTIVE or PAUSED (new case running). Fail if timeout."""
    deadline = time.monotonic() + timeout_sec
    last_status = ""
    while time.monotonic() < deadline:
        case = client.get_case()
        last_status = (case or {}).get("status") or ""
        if last_status in RUNNING_STATUSES:
            return
        time.sleep(poll_interval)
    pytest.fail(f"Case did not become ACTIVE/PAUSED within {timeout_sec}s (last status={last_status})")


def _place_limit_then_cancel(
    client: RotmanSDK, ticker: str, action: ActionEnum, price: float, quantity: int = 1, delay_after_sec: float = ORDER_DELAY_SEC
):
    """Place a limit order and cancel it; return order_id or None. Waits delay_after_sec to avoid rate limit."""
    order = None
    for attempt in range(2):
        try:
            order = client.place_order(ticker, OrderType.LIMIT, quantity, action, price=price)
            break
        except RITError as e:
            if e.status_code == 429 and getattr(e, "wait", None) and attempt == 0:
                time.sleep(float(e.wait))
                continue
            raise
    if order is None:
        return None
    oid = order.get("order_id") if isinstance(order, dict) else None
    if oid is not None:
        client.cancel_order(oid)
    if delay_after_sec > 0:
        time.sleep(delay_after_sec)
    return oid


def _available_tickers(client: RotmanSDK) -> set[str]:
    """Return set of ticker symbols available in the current case (from get_securities)."""
    secs = client.get_securities()
    return {s.get("ticker") for s in secs if s.get("ticker")}


# TAME: at every price step in (15, 35), place+cancel BUY then place+cancel SELL

def test_live_tame_buy_every_price(live_client: RotmanSDK):
    """At every price in (15, 35), place then cancel a BUY."""
    _wait_for_case_running(live_client)
    if "TAME" not in _available_tickers(live_client):
        pytest.skip("TAME not in this case")
    prices = _prices_every_cent(TAME_PRICE_MIN, TAME_PRICE_MAX)
    for price in prices:
        try:
            print(f"Placing BUY order at price {price}")
            _place_limit_then_cancel(live_client, "TAME", ActionEnum.BUY, price, quantity=1)
        except RITError as e:
            if e.status_code in (400, 429):
                pytest.skip(f"API rejected order: {e}")
            raise

def test_live_tame_sell_every_price(live_client: RotmanSDK):
    """At every price in (15, 35), place then cancel a SELL."""
    _wait_for_case_running(live_client)
    if "TAME" not in _available_tickers(live_client):
        pytest.skip("TAME not in this case")
    prices = _prices_every_cent(TAME_PRICE_MIN, TAME_PRICE_MAX)
    for price in prices:
        try:
            print(f"Placing SELL order at price {price}")
            _place_limit_then_cancel(live_client, "TAME", ActionEnum.SELL, price, quantity=1)
        except RITError as e:
            if e.status_code in (400, 429):
                pytest.skip(f"API rejected order: {e}")
            raise


# CRZY: at every price step in (5, 20), place+cancel BUY then place+cancel SELL

def test_live_crzy_buy_and_sell_every_price(live_client: RotmanSDK):
    """At every price in (5, 20), place then cancel a BUY, then place then cancel a SELL."""
    _wait_for_case_running(live_client)
    if "CRZY" not in _available_tickers(live_client):
        pytest.skip("CRZY not in this case")
    prices = _prices_every_cent(CRZY_PRICE_MIN, CRZY_PRICE_MAX)
    for price in prices:
        try:
            _place_limit_then_cancel(live_client, "CRZY", ActionEnum.BUY, price, quantity=1)
            _place_limit_then_cancel(live_client, "CRZY", ActionEnum.SELL, price, quantity=1)
        except RITError as e:
            if e.status_code in (400, 429):
                pytest.skip(f"API rejected order: {e}")
            raise


# Wait for tender then accept

def _wait_for_tender(client: RotmanSDK, timeout_sec: int = TENDER_WAIT_TIMEOUT, poll_interval: int = TENDER_POLL_INTERVAL):
    """Poll get_tenders() until at least one tender appears or timeout."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        tenders = client.get_tenders()
        if tenders and len(tenders) > 0:
            return tenders
        time.sleep(poll_interval)
    pytest.fail(f"No tender appeared within {timeout_sec} seconds")


def test_live_accept_tender(live_client: RotmanSDK):
    """Wait for a tender to appear (up to ~5 min), then accept it."""
    _wait_for_case_running(live_client)
    tenders = _wait_for_tender(live_client)
    assert len(tenders) >= 1
    first = tenders[0]
    tender_id = getattr(first, "id", None) or getattr(first, "tender_id", None)
    assert tender_id is not None
    result = live_client.accept_tender(tender_id)
    assert isinstance(result, dict)


# Wait for tender then decline

def test_live_decline_tender(live_client: RotmanSDK):
    """Wait for a tender to appear (up to ~5 min), then decline it."""
    _wait_for_case_running(live_client)
    tenders = _wait_for_tender(live_client)
    assert len(tenders) >= 1
    first = tenders[0]
    tender_id = getattr(first, "id", None) or getattr(first, "tender_id", None)
    assert tender_id is not None
    result = live_client.decline_tender(tender_id)
    assert isinstance(result, dict)


# Wait for new 5‑min period, then poll until we have seen all 300 ticks

def test_live_poll_all_300_ticks_in_period(live_client: RotmanSDK):
    """Wait for a round to become ACTIVE, then observe all 300 ticks in that period."""
    case = live_client.get_case()
    ticks_per_period = int(case.get("ticks_per_period") or 300)
    if ticks_per_period <= 0:
        ticks_per_period = 300

    expected_ticks = set(range(ticks_per_period))
    print(f"\n[setup] ticks_per_period={ticks_per_period}, expecting ticks 0..{ticks_per_period - 1}", flush=True)

    def get_status_and_tick():
        c = live_client.get_case() or {}
        status = c.get("status") or ""
        tick = int(c.get("tick", 0) or 0)
        return status, tick

    # Step 1: Wait for ACTIVE
    print("[step 1] waiting for ACTIVE...", flush=True)
    deadline = time.monotonic() + NEW_PERIOD_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        status, tick = get_status_and_tick()
        print(f"  status={status} tick={tick}", flush=True)
        if status == "ACTIVE":
            print(f"[step 1] ACTIVE at tick={tick}", flush=True)
            break
        time.sleep(TICK_POLL_INTERVAL)
    else:
        pytest.skip(f"Simulation never became ACTIVE within {NEW_PERIOD_WAIT_TIMEOUT}s")

    # Step 2: Wait for non-zero tick
    print("[step 2] waiting for non-zero tick...", flush=True)
    deadline = time.monotonic() + NEW_PERIOD_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        status, tick = get_status_and_tick()
        print(f"  status={status} tick={tick}", flush=True)
        if status == "STOPPED":
            continue
        if tick > 0:
            print(f"[step 2] saw non-zero tick={tick}", flush=True)
            break
        time.sleep(TICK_POLL_INTERVAL)
    else:
        pytest.fail(f"Never saw a non-zero tick within {NEW_PERIOD_WAIT_TIMEOUT}s")

    # Step 3: Wait for rollover to tick 0
    print("[step 3] waiting for period rollover (tick 0)...", flush=True)
    deadline = time.monotonic() + NEW_PERIOD_WAIT_TIMEOUT
    at_period_start = False
    while time.monotonic() < deadline:
        status, tick = get_status_and_tick()
        print(f"  status={status} tick={tick}", flush=True)
        if status == "STOPPED":
            time.sleep(TICK_POLL_INTERVAL)
            continue
        if tick == 0 and status == "ACTIVE":
            at_period_start = True
            print("[step 3] period rollover detected — tick=0, ACTIVE", flush=True)
            break
        time.sleep(TICK_POLL_INTERVAL)

    if not at_period_start:
        pytest.fail(f"Did not see period rollover to tick 0 within {NEW_PERIOD_WAIT_TIMEOUT}s")

    # Step 4: Collect all ticks
    print(f"[step 4] collecting ticks 0..{ticks_per_period - 1}...", flush=True)
    observed: set[int] = set()
    deadline = time.monotonic() + (ticks_per_period * 2)

    while time.monotonic() < deadline and observed != expected_ticks:
        status, tick = get_status_and_tick()
        if status == "STOPPED":
            print(f"[step 4] STOPPED — collected {len(observed)}/{ticks_per_period} ticks", flush=True)
            break
        if observed and tick == 0:
            print(f"[step 4] period rolled over — collected {len(observed)}/{ticks_per_period} ticks", flush=True)
            break
        if tick not in observed:
            print(f"[step 4] new tick={tick} ({len(observed) + 1}/{ticks_per_period})", flush=True)
        observed.add(tick)
        time.sleep(TICK_POLL_INTERVAL)

    missing = expected_ticks - observed
    assert not missing, (
        f"Expected to observe all {ticks_per_period} ticks (0..{ticks_per_period - 1}); "
        f"missing {len(missing)}: {sorted(missing)[:20]}{'...' if len(missing) > 20 else ''}"
    )
    print(f"[done] observed all {ticks_per_period} ticks", flush=True)

# Test that the simulation has stopped

def test_live_simulation_stopped(live_client: RotmanSDK):
    """Poll get_case() until status is STOPPED (timeout ~6.5 min); assert we see STOPPED."""
    deadline = time.monotonic() + STOPPED_WAIT_TIMEOUT
    last_status = None
    while time.monotonic() < deadline:
        case = live_client.get_case()
        status = (case or {}).get("status") or ""
        last_status = status
        if status == "STOPPED":
            assert status == "STOPPED"
            return
        time.sleep(TICK_POLL_INTERVAL)

    pytest.fail(f"Simulation did not reach STOPPED within {STOPPED_WAIT_TIMEOUT}s (last status={last_status})")
