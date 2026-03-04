"""
Rotman Interactive Trader (RIT) REST API client.

Aligns with the RIT API documentation (e.g. ritc.readthedocs.io):
- Case, trader, limits, assets, securities, order book, OHLC, time & sales
- Orders (get, place, cancel single or bulk)
- Tenders (list, accept, decline)
- Leases (list, create, delete)
- News
"""

from __future__ import annotations

import requests
from typing import Any, Optional

from utilities import (
    ActionEnum,
    OrderType,
    Position,
    TenderOrder,
    Level,
    OrderBook,
    Order,
    Case,
    Trader,
    TradingLimit,
    News,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RITError(Exception):
    """Raised when the RIT API returns an error (4xx/5xx)."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        wait: Optional[float] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.wait = wait


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RotmanSDK:
    """
    Client for the Rotman Interactive Trader REST API.

    Usage:
        client = RotmanSDK(API_KEY="...", HOST="http://host:9999/v1")
        case = client.get_case()
        securities = client.get_securities()
        book = client.get_securities_book(ticker="ALGO")
    """

    def __init__(
        self,
        API_KEY: str,
        HOST: str = "http://192.168.64.9:9999/v1",
        timeout: float = 10.0,
    ) -> None:
        self.HOST = HOST.rstrip("/")
        self.HEADERS = {"X-API-KEY": API_KEY, "Accept": "application/json"}
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"<RotmanSDK host={self.HOST}>"

    def __str__(self) -> str:
        return f"RotmanSDK({self.HOST})"

    # ----- Low-level HTTP -----

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[dict[str, Any]] = None,
        raise_for_status: bool = True,
    ) -> Any:
        url = f"{self.HOST}{endpoint}"
        kwargs: dict[str, Any] = {
            "headers": self.HEADERS,
            "timeout": self.timeout,
        }
        if params:
            kwargs["params"] = {k: v for k, v in params.items() if v is not None}
        if method == "POST":
            kwargs.setdefault("params", {})
            kwargs["data"] = ""
        resp = requests.request(method, url, **kwargs)
        if raise_for_status and not resp.ok:
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            raise RITError(
                message=body.get("message", resp.text or resp.reason or f"HTTP {resp.status_code}"),
                status_code=resp.status_code,
                code=body.get("code"),
                wait=body.get("wait"),
            )
        if not resp.text:
            return {}
        return resp.json()

    def _get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self._request("GET", endpoint, params=params)

    def _post(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self._request("POST", endpoint, params=params or {})

    def _delete(self, endpoint: str) -> Any:
        return self._request("DELETE", endpoint)

    # ----- Case & meta -----

    def get_case(self) -> dict[str, Any]:
        """Get current case/session (name, tick, status, period, etc.)."""
        return self._get("/case")

    def get_case_typed(self) -> Case:
        """Get current case as a Case dataclass."""
        return Case.from_api(self.get_case())

    def get_tick(self) -> int:
        """Current simulation tick; 0 if stopped."""
        case = self.get_case()
        if isinstance(case, dict):
            return int(case.get("tick", 0) or 0)
        return 0

    def get_trader(self) -> dict[str, Any]:
        """Get currently logged-in trader."""
        return self._get("/trader")

    def get_trader_typed(self) -> Trader:
        """Get current trader as a Trader dataclass."""
        return Trader.from_api(self.get_trader())

    def get_limits(self) -> list[dict[str, Any]]:
        """Get trading/exposure limits for the case."""
        raw = self._get("/limits")
        return raw if isinstance(raw, list) else [raw] if raw else []

    def get_limits_typed(self) -> list[TradingLimit]:
        """Get limits as list of TradingLimit dataclasses."""
        return [TradingLimit.from_api(d) for d in self.get_limits()]

    # ----- Assets -----

    def get_assets(self, ticker: Optional[str] = None) -> list[dict[str, Any]]:
        """Get list of available assets; optionally filter by ticker."""
        raw = self._get("/assets", params={"ticker": ticker})
        return raw if isinstance(raw, list) else [raw] if raw else []

    def get_assets_history(
        self,
        ticker: Optional[str] = None,
        period: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get asset activity log (RIT API v1.0.3+)."""
        raw = self._get(
            "/assets/history",
            params={"ticker": ticker, "period": period, "limit": limit},
        )
        return raw if isinstance(raw, list) else []

    def get_leases(self, lease_id: Optional[int] = None) -> Any:
        """
        List all leases, or get a single lease by id.
        Returns list of lease dicts, or one dict if lease_id is set.
        """
        if lease_id is not None:
            return self._get(f"/leases/{lease_id}")
        return self._get("/leases") or []

    def delete_leases(self, lease_id: int) -> dict[str, Any]:
        """Unlease an asset by its lease id."""
        return self._delete(f"/leases/{lease_id}") or {}

    def post_leases(
        self,
        ticker: str,
        *,
        lease_id: Optional[int] = None,
        from1: Optional[str] = None,
        quantity1: Optional[float] = None,
        from2: Optional[str] = None,
        quantity2: Optional[float] = None,
        from3: Optional[str] = None,
        quantity3: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Lease or use an asset. For refineries etc. pass from1, quantity1, ...
        See RIT API docs for asset-specific parameters.
        """
        params: dict[str, Any] = {"ticker": ticker}
        if lease_id is not None:
            params["id"] = lease_id
        if from1 is not None:
            params["from1"] = from1
        if quantity1 is not None:
            params["quantity1"] = quantity1
        if from2 is not None:
            params["from2"] = from2
        if quantity2 is not None:
            params["quantity2"] = quantity2
        if from3 is not None:
            params["from3"] = from3
        if quantity3 is not None:
            params["quantity3"] = quantity3
        return self._post("/leases", params=params)

    # ----- Securities -----

    def get_securities(self, ticker: Optional[str] = None) -> list[dict[str, Any]]:
        """Get list of securities and associated positions (bid, ask, position, etc.)."""
        raw = self._get("/securities", params={"ticker": ticker})
        return raw if isinstance(raw, list) else [raw] if raw else []

    def get_securities_book(
        self,
        ticker: str,
        limit: Optional[int] = 20,
    ) -> OrderBook:
        """Get order book for a security (bids and asks as Level lists)."""
        raw = self._get("/securities/book", params={"ticker": ticker, "limit": limit or 20})
        bids = [Level.from_api(b) for b in raw.get("bids", [])]
        asks = [Level.from_api(a) for a in raw.get("asks", [])]
        return OrderBook(bids=bids, asks=asks)

    def get_book(self, ticker: str, limit: int = 20) -> OrderBook:
        """Alias for get_securities_book."""
        return self.get_securities_book(ticker=ticker, limit=limit)

    def get_securities_history(
        self,
        ticker: str,
        period: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get OHLC history for a security."""
        raw = self._get(
            "/securities/history",
            params={"ticker": ticker, "period": period, "limit": limit},
        )
        return raw if isinstance(raw, list) else []

    def get_ohlc(
        self,
        ticker: str,
        period: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Alias for get_securities_history."""
        return self.get_securities_history(ticker=ticker, period=period, limit=limit)

    def get_securities_tas(
        self,
        ticker: str,
        *,
        after: Optional[int] = None,
        period: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get time-and-sales (tick) history for a security. Use after=id for incremental."""
        params: dict[str, Any] = {"ticker": ticker}
        if after is not None:
            params["after"] = after
        if period is not None:
            params["period"] = period
        if limit is not None:
            params["limit"] = limit
        raw = self._get("/securities/tas", params=params)
        return raw if isinstance(raw, list) else []

    # ----- Positions (convenience) -----

    def get_positions(self) -> dict[str, Position]:
        """Get positions per ticker (from securities), using British spelling unrealised/realised."""
        secs = self.get_securities()
        return {s["ticker"]: Position.from_api(s) for s in secs if s.get("ticker")}

    # ----- Orders -----

    def get_orders(
        self,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get orders; default status is OPEN. Use get_order(id) for a single order."""
        raw = self._get("/orders", params={"status": status})
        return raw if isinstance(raw, list) else []

    def get_order(self, order_id: int) -> Optional[dict[str, Any]]:
        """Get a single order by id. Returns None if 404."""
        try:
            return self._get(f"/orders/{order_id}")
        except RITError as e:
            if e.status_code == 404:
                return None
            raise

    def get_all_orders(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        """Alias for get_orders."""
        return self.get_orders(status=status)

    def get_specific_order(self, order_id: int) -> Optional[dict[str, Any]]:
        """Alias for get_order."""
        return self.get_order(order_id)

    def place_order(
        self,
        ticker: str,
        order_type: OrderType,
        quantity: int,
        action: ActionEnum,
        price: Optional[float] = None,
    ) -> dict[str, Any]:
        """Place an order (limit or market)."""
        if ticker == "CRZY" and quantity > 25_000:
            raise ValueError("CRZY quantity must be <= 25,000")
        if ticker == "TAME" and quantity > 10_000:
            raise ValueError("TAME quantity must be <= 10,000")
        params: dict[str, Any] = {
            "ticker": ticker,
            "type": order_type.value,
            "quantity": quantity,
            "action": action.value,
        }
        if order_type == OrderType.LIMIT:
            if price is None:
                raise ValueError("LIMIT orders require a price")
            params["price"] = price
        return self._post("/orders", params=params)

    def post_orders(
        self,
        ticker: str,
        order_type: OrderType,
        quantity: int,
        action: ActionEnum,
        price: Optional[float] = None,
    ) -> dict[str, Any]:
        """Alias for place_order (RIT-style name)."""
        return self.place_order(ticker, order_type, quantity, action, price)

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        """Cancel a single order by id."""
        try:
            return self._delete(f"/orders/{order_id}") or {"success": True}
        except RITError as e:
            if e.status_code == 404:
                return {"success": False}
            raise

    def delete_orders(self, order_id: int) -> dict[str, Any]:
        """Alias for cancel_order (RIT-style name)."""
        return self.cancel_order(order_id)

    def cancel_orders(
        self,
        *,
        all: bool = False,
        ticker: Optional[str] = None,
        ids: Optional[list[int] | str] = None,
    ) -> dict[str, Any]:
        """
        Bulk cancel orders. Exactly one of: all=True, ticker="X", or ids=[1,2,3].
        Returns result with cancelled_order_ids when supported.
        """
        if all:
            return self._post("/commands/cancel", params={"all": 1}) or {}
        if ticker is not None:
            return self._post("/commands/cancel", params={"ticker": ticker}) or {}
        if ids is not None:
            id_str = ids if isinstance(ids, str) else ",".join(map(str, ids))
            return self._post("/commands/cancel", params={"ids": id_str}) or {}
        raise ValueError("Provide one of: all=True, ticker=..., or ids=...")

    def kill_all(self) -> dict[str, Any]:
        """Cancel all open orders. Alias for cancel_orders(all=True)."""
        return self.cancel_orders(all=True)

    # ----- Order helpers -----

    def buy_market(self, ticker: str, quantity: int) -> dict[str, Any]:
        return self.place_order(ticker, OrderType.MARKET, quantity, ActionEnum.BUY)

    def sell_market(self, ticker: str, quantity: int) -> dict[str, Any]:
        return self.place_order(ticker, OrderType.MARKET, quantity, ActionEnum.SELL)

    def buy_limit(self, ticker: str, quantity: int, limit_price: float) -> dict[str, Any]:
        return self.place_order(
            ticker, OrderType.LIMIT, quantity, ActionEnum.BUY, price=limit_price
        )

    def sell_limit(self, ticker: str, quantity: int, limit_price: float) -> dict[str, Any]:
        return self.place_order(
            ticker, OrderType.LIMIT, quantity, ActionEnum.SELL, price=limit_price
        )

    # ----- Tenders -----

    def get_tenders(self) -> Optional[list[TenderOrder]]:
        """Get list of active tenders. Returns None if empty/unavailable."""
        raw = self._get("/tenders")
        if not raw:
            return None
        return [TenderOrder.from_api(t) for t in raw]

    def accept_tender(self, tender_id: int) -> dict[str, Any]:
        """Accept a tender by id."""
        return self._post(f"/tenders/{tender_id}") or {}

    def post_tenders(self, tender_id: int, price: Optional[float] = None) -> dict[str, Any]:
        """Alias for accept_tender (RIT-style). Some APIs require price for non-fixed tenders."""
        params: dict[str, Any] = {}
        if price is not None:
            params["price"] = price
        return self._post(f"/tenders/{tender_id}", params=params or None) or {}

    def decline_tender(self, tender_id: int) -> dict[str, Any]:
        """Decline a tender by id."""
        try:
            self._delete(f"/tenders/{tender_id}")
            return {"success": True}
        except RITError as e:
            if e.status_code == 400:
                return {"success": False}
            raise

    def delete_tenders(self, tender_id: int) -> dict[str, Any]:
        """Alias for decline_tender (RIT-style name)."""
        return self.decline_tender(tender_id)

    def easy_accept_tender(self) -> Optional[TenderOrder]:
        """Accept the first available tender if any; returns that TenderOrder or None."""
        tenders = self.get_tenders()
        if not tenders:
            return None
        t = tenders[0]
        result = self.accept_tender(t.id)
        return t if result.get("success") else None

    def easy_decline_tender(self) -> Optional[dict[str, Any]]:
        """Decline the first available tender; returns {'tender_id': id} or None."""
        tenders = self.get_tenders()
        if not tenders:
            return None
        ok = self.decline_tender(tenders[0].id).get("success", False)
        return {"tender_id": tenders[0].id} if ok else None

    # ----- News -----

    def get_news(
        self,
        *,
        since: Optional[int] = None,
        after: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Get recent news. Use since=news_id or after=news_id (API version dependent).
        """
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = since
        if after is not None:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        raw = self._get("/news", params=params or None)
        return raw if isinstance(raw, list) else []

    def get_news_typed(
        self,
        *,
        since: Optional[int] = None,
        after: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[News]:
        """Get news as list of News dataclasses."""
        return [News.from_api(n) for n in self.get_news(since=since, after=after, limit=limit)]
