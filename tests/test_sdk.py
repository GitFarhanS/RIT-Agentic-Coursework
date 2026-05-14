"""
Tests for RotmanSDK and RITError (``coursework.infrastructure.rotman_client``).

- Unit tests (no network): RITError, SDK init, validation (e.g. LIMIT price, cancel_orders args).
- Live tests: use API key from .env (API_KEY or RIT_API_KEY); skip if unset or API unreachable.
  Ensure .env contains API_KEY=your_key (and optionally RIT_HOST=...). Then: pytest tests/test_sdk.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Ensure src on path so ``coursework`` resolves when not installed editable
_root = Path(__file__).resolve().parent.parent
_src = _root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


def _load_dotenv():
    """Load .env from project root; set RIT_API_KEY from API_KEY if present."""
    env_file = _root / ".env"
    if not env_file.is_file():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)
                if key == "API_KEY" and "RIT_API_KEY" not in os.environ:
                    os.environ["RIT_API_KEY"] = value


_load_dotenv()

import pytest

from coursework.domain.models import ActionEnum, OrderType
from coursework.infrastructure.rotman_client import RITError, RotmanSDK


# Live client fixture

def _get_live_client():
    """Create SDK client from .env / env (RIT_API_KEY). Returns None if not set."""
    key = os.environ.get("RIT_API_KEY", "").strip()
    if not key:
        return None
    host = os.environ.get("RIT_HOST", "http://192.168.64.9:9999/v1").strip()
    return RotmanSDK(API_KEY=key, HOST=host)


def _is_live_available():
    """True if RIT_API_KEY is set and API responds (get_case succeeds)."""
    client = _get_live_client()
    if client is None:
        return False
    try:
        client.get_case()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def live_client():
    """Live RIT client from env. Skips test if RIT_API_KEY unset or API unreachable."""
    if not _is_live_available():
        pytest.skip("RIT_API_KEY not set or RIT API unreachable")
    return _get_live_client()


# RITError (unit)

def test_rit_error_attributes():
    e = RITError("Bad request", status_code=400, code="BAD", wait=1.5)
    assert str(e) == "Bad request"
    assert e.status_code == 400
    assert e.code == "BAD"
    assert e.wait == 1.5


def test_rit_error_optional_args():
    e = RITError("Server error")
    assert e.status_code is None
    assert e.code is None
    assert e.wait is None


# RotmanSDK init (unit)

def test_sdk_init_strips_trailing_slash():
    client = RotmanSDK(API_KEY="key1", HOST="http://host:9999/v1/")
    assert client.HOST == "http://host:9999/v1"
    assert client.HEADERS["X-API-KEY"] == "key1"
    assert client.timeout == 10.0


def test_sdk_repr_str():
    client = RotmanSDK(API_KEY="k", HOST="http://h/v1")
    assert "<RotmanSDK host=" in repr(client)
    assert "http://h/v1" in str(client)



# Validation (unit, no network)

def test_place_order_limit_requires_price():
    client = RotmanSDK(API_KEY="k", HOST="http://h/v1")
    with pytest.raises(ValueError, match="LIMIT orders require a price"):
        client.place_order("CRZY", OrderType.LIMIT, 100, ActionEnum.BUY, price=None)


def test_place_order_crzy_quantity_limit():
    client = RotmanSDK(API_KEY="k", HOST="http://h/v1")
    with pytest.raises(ValueError, match="CRZY quantity must be <= 25,000"):
        client.place_order("CRZY", OrderType.MARKET, 30_000, ActionEnum.BUY)


def test_place_order_tame_quantity_limit():
    client = RotmanSDK(API_KEY="k", HOST="http://h/v1")
    with pytest.raises(ValueError, match="TAME quantity must be <= 10,000"):
        client.place_order("TAME", OrderType.MARKET, 15_000, ActionEnum.SELL)


def test_cancel_orders_requires_one_option():
    client = RotmanSDK(API_KEY="k", HOST="http://h/v1")
    with pytest.raises(ValueError, match="Provide one of"):
        client.cancel_orders()


# Live: Case & meta

def test_live_get_case(live_client):
    case = live_client.get_case()
    assert isinstance(case, dict)
    assert "tick" in case
    assert "status" in case
    assert case["status"] in ("ACTIVE", "PAUSED", "STOPPED")


def test_live_get_tick(live_client):
    tick = live_client.get_tick()
    assert isinstance(tick, int)
    assert tick >= 0


def test_live_check_api_key(live_client):
    assert live_client.check_api_key() is True


def test_live_get_limits(live_client):
    limits = live_client.get_limits()
    assert isinstance(limits, list)
    for lim in limits:
        assert isinstance(lim, dict)


# Live: Securities

def test_live_get_securities(live_client):
    secs = live_client.get_securities()
    assert isinstance(secs, list)
    for s in secs:
        assert isinstance(s, dict)
        assert "ticker" in s


def test_live_get_securities_book(live_client):
    # LT3 case has CRZY and TAME
    secs = live_client.get_securities()
    tickers = [s["ticker"] for s in secs if s.get("ticker")]
    if not tickers:
        pytest.skip("No securities returned")
    ticker = tickers[0]
    book = live_client.get_securities_book(ticker, limit=5)
    assert hasattr(book, "bids") and hasattr(book, "asks")
    assert isinstance(book.bids, list)
    assert isinstance(book.asks, list)


def test_live_get_securities_tas(live_client):
    secs = live_client.get_securities()
    tickers = [s["ticker"] for s in secs if s.get("ticker")]
    if not tickers:
        pytest.skip("No securities returned")
    tas = live_client.get_securities_tas(tickers[0], limit=10)
    assert isinstance(tas, list)


def test_live_get_positions(live_client):
    pos = live_client.get_positions()
    assert isinstance(pos, dict)
    for ticker, p in pos.items():
        assert hasattr(p, "position") and hasattr(p, "ticker")


# Live: Orders (read-only + one round-trip)

def test_live_get_orders(live_client):
    orders = live_client.get_orders()
    assert isinstance(orders, list)


def test_live_get_orders_with_status(live_client):
    for status in ("OPEN", "TRANSACTED", "CANCELLED"):
        orders = live_client.get_orders(status=status)
        assert isinstance(orders, list)


def test_live_get_order_nonexistent_returns_none(live_client):
    # Assume no order has this id
    out = live_client.get_order(99999999)
    assert out is None


def test_live_place_and_cancel_order(live_client):
    """Place a small limit order then cancel it so we don't leave state."""
    secs = live_client.get_securities()
    tickers = [s["ticker"] for s in secs if s.get("ticker")]
    if not tickers:
        pytest.skip("No securities returned")
    ticker = tickers[0]
    # Use a limit order within valid range (e.g. LT3 price must be > 5 and < 20) but far from market so it doesn't fill
    try:
        order = live_client.place_order(
            ticker, OrderType.LIMIT, 1, ActionEnum.BUY, price=5.01
        )
    except RITError as e:
        if e.status_code in (400, 429):
            pytest.skip(f"API rejected order: {e}")
        raise
    assert isinstance(order, dict)
    oid = order.get("order_id")
    if oid is not None:
        result = live_client.cancel_order(oid)
        # API may return success=False if order was already filled or cancelled
        assert isinstance(result, dict)


# Live: Tenders

# Poll interval when waiting for a tender (seconds)
_TENDER_POLL_INTERVAL = 1
# Max time to wait for a tender to appear (5 minutes)
_TENDER_WAIT_TIMEOUT = 300


def _wait_for_tender(client, timeout_sec: int = _TENDER_WAIT_TIMEOUT, poll_interval: int = _TENDER_POLL_INTERVAL):
    """Poll get_tenders() until at least one tender appears or timeout. Returns list of tenders or raises."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        tenders = client.get_tenders()
        if tenders and len(tenders) > 0:
            return tenders
        time.sleep(poll_interval)
    pytest.fail(f"No tender appeared within {timeout_sec} seconds (polled every {poll_interval}s)")


def test_live_get_tenders(live_client):
    """Wait up to 5 minutes for a tender to appear; fail if none shown."""
    tenders = _wait_for_tender(live_client)
    assert isinstance(tenders, list)
    assert len(tenders) >= 1
    for t in tenders:
        assert hasattr(t, "id") and hasattr(t, "ticker")


# Live: Bulk cancel (no-op if no orders)

def test_live_kill_all(live_client):
    # Just ensure it doesn't raise; may cancel 0 orders
    result = live_client.kill_all()
    assert isinstance(result, dict) or result is None
