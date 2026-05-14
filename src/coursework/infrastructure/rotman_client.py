"""
Rotman Interactive Trader (RIT) REST API client.
Case, limits, securities (book, TAS), orders, tenders.
"""

from __future__ import annotations

import requests
from typing import Any, Optional

from coursework.domain.models import (
    ActionEnum,
    OrderType,
    Position,
    TenderOrder,
    Level,
    OrderBook,
)



# Errors



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



# Client



class RotmanSDK:
    """
    Client for the Rotman Interactive Trader REST API.

    Usage:
        client = RotmanSDK(API_KEY="TPIAOJIF", HOST="http://host:9999/v1")
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

    # Low-level HTTP

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

    # Case & meta

    def get_case(self) -> dict[str, Any]:
        """Get current case/session (name, tick, status, period, etc.)."""
        return self._get("/case")

    def get_tick(self) -> int:
        """Current simulation tick; 0 if stopped."""
        case = self.get_case()
        return int(case.get("tick", 0) or 0) if isinstance(case, dict) else 0

    def check_api_key(self) -> bool:
        """Confirm API key works by calling get_case(). Returns True or raises RITError."""
        self.get_case()
        return True

    def get_limits(self) -> list[dict[str, Any]]:
        """Get trading/exposure limits for the case."""
        raw = self._get("/limits")
        return raw if isinstance(raw, list) else [raw] if raw else []

    # Securities

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

    def get_securities_tas(
        self,
        ticker: str,
        *,
        after: Optional[int] = None,
        period: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Time-and-sales (tick) history. Use after=id for incremental."""
        raw = self._get("/securities/tas", params={"ticker": ticker, "after": after, "period": period, "limit": limit})
        return raw if isinstance(raw, list) else []

    # Positions (convenience)

    def get_positions(self) -> dict[str, Position]:
        """Get positions per ticker (from securities), using British spelling unrealised/realised."""
        secs = self.get_securities()
        return {s["ticker"]: Position.from_api(s) for s in secs if s.get("ticker")}

    # Orders

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

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        """Cancel a single order by id."""
        try:
            return self._delete(f"/orders/{order_id}") or {"success": True}
        except RITError as e:
            if e.status_code == 404:
                return {"success": False}
            raise

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

    # Tenders

    def get_tenders(self) -> Optional[list[TenderOrder]]:
        """Get list of active tenders. Returns None if empty/unavailable."""
        raw = self._get("/tenders")
        if not raw:
            return None
        return [TenderOrder.from_api(t) for t in raw]

    def accept_tender(self, tender_id: int, price: Optional[float] = None) -> dict[str, Any]:
        """Accept a tender by id. Pass price for non-fixed-bid tenders if required."""
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
