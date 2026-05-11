"""Deliberative broker → tender evaluation pipeline (mixin inheritance)."""

from __future__ import annotations

import time
from typing import Any

from coursework.agents.common import get_last_trade_price, get_mid_price
from coursework.agents.deliberative.records import TenderRecord, Unwind
from coursework.agents.deliberative.settings import DeliberativeSettings
from coursework.domain.models import ActionEnum, OrderType
from coursework.infrastructure.rotman_client import RITError, RotmanSDK

class BrokerHelpers:
    def __init__(self, client: RotmanSDK, settings: DeliberativeSettings) -> None:
        self.client = client
        self.settings = settings

    def _retry_read(self, label: str, fn):
        """Retry a broker read up to self.settings.read_retries times before raising."""
        last_err: Exception | None = None
        for attempt in range(self.settings.read_retries):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt + 1 < self.settings.read_retries:
                    time.sleep(self.settings.read_retry_sleep)
        raise RuntimeError(f"{label} failed after {self.settings.read_retries} attempts: {last_err}")

    def position(self, ticker: str) -> int:
        """How many shares do I currently hold for this ticker?"""
        def _read() -> int:
            p = self.client.get_positions().get(ticker)
            return int(p.position) if p else 0
        return self._retry_read(f"position({ticker})", _read)

    def all_positions(self) -> dict[str, int]:
        """Position for every ticker I hold."""
        def _read() -> dict[str, int]:
            return {t: int(p.position) for t, p in self.client.get_positions().items()}
        return self._retry_read("all_positions()", _read)

    def best_bid_ask(self, ticker: str) -> tuple[float, float]:
        """Current top bid and ask price."""
        def _read() -> tuple[float, float]:
            secs = self.client.get_securities(ticker=ticker)
            if not secs:
                return 0.0, 0.0
            s = secs[0] if isinstance(secs, list) else secs
            return float(s.get("bid") or 0), float(s.get("ask") or 0)
        return self._retry_read(f"best_bid_ask({ticker})", _read)

    def open_limit_ids(self, ticker: str, side: ActionEnum | None = None) -> list[int]:
        """IDs of all resting limit orders for this ticker (optionally filtered by side)."""
        try:
            ords = self.client.get_orders(status="OPEN")
        except Exception:
            return []
        out = []
        for o in ords:
            if str(o.get("ticker")) != ticker:
                continue
            if str(o.get("type") or "").upper() != "LIMIT":
                continue
            if side is not None and str(o.get("action") or "").upper() != side.value.upper():
                continue
            oid = o.get("order_id") or o.get("id")
            if oid is not None:
                out.append(int(oid))
        return out

    def resting_qty(self, ticker: str, side: ActionEnum) -> int:
        """How many shares are sitting in open limit orders (on one side)?"""
        ords = self._retry_read(
            "resting_qty.get_orders()",
            lambda: self.client.get_orders(status="OPEN"),
        )
        total = 0
        for o in ords:
            if str(o.get("ticker")) != ticker:
                continue
            if str(o.get("type") or "").upper() != "LIMIT":
                continue
            if str(o.get("action") or "").upper() != side.value.upper():
                continue
            total += int(o.get("quantity_remaining") or o.get("quantity") or 0)
        return total

    def cancel_all_limits(self, ticker: str) -> None:
        """Nuke all open limit orders for a ticker."""
        for oid in self.open_limit_ids(ticker):
            try:
                self.client.cancel_order(oid)
            except RITError:
                pass

    def cancel_all_and_wait(self, ticker: str, timeout: float = 2.0) -> None:
        """Cancel all limits and block until the book confirms they're gone."""
        self.cancel_all_limits(ticker)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.open_limit_ids(ticker):
                return
            time.sleep(0.05)

    @staticmethod
    def ticks_remaining(case: dict[str, Any]) -> int:
        """How many ticks are left in the entire game?"""
        tpp    = int(case.get("ticks_per_period") or 300)
        tp     = int(case.get("total_periods") or 1)
        period = int(case.get("period") or 1)
        tick   = int(case.get("tick") or 0)
        return max(0, tp * tpp - ((period - 1) * tpp + tick))


# =============================================================================
# 4. LIABILITY CHECK
# =============================================================================
# remaining_liability():
#   current_pos = my current position
#   delta = current_pos - baseline_pos  ← where I was BEFORE the tender
#
#   if delta > 0 → I'm too long  → need to SELL delta shares
#   if delta < 0 → I'm too short → need to BUY |delta| shares
#   if delta = 0 → done 
#
# This is the core rule: never trade past the baseline.
# Every order checks this first.
# =============================================================================

class LiabilityChecker(BrokerHelpers):

    def remaining_liability(self, u: Unwind) -> tuple[int, ActionEnum]:
        """
        Shares left to trade to bring position back to baseline.
        Returns (quantity, action) — action can flip if we somehow overshot.
        """
        cur   = self.position(u.ticker)
        delta = cur - u.baseline_pos

        if delta == 0:
            return 0, u.side      # done ✅
        if delta > 0:
            return delta, ActionEnum.SELL   # too long → sell
        return -delta, ActionEnum.BUY       # too short → buy


# =============================================================================
# 5. ORDER PLACEMENT
# =============================================================================
# market_to_target(want, side):
#   while shares_still_needed > 0:
#     check remaining liability (never exceed it)
#     send market order for min(want, max_chunk, liability_left)
#
# post_passive(qty):
#   check how much liability is left minus already-resting orders
#   post limit order just inside the spread
#   → if selling: just below ask
#   → if buying:  just above bid
# =============================================================================

class OrderPlacer(LiabilityChecker):

    def _order_filled_qty(self, order_id: int, fallback: int) -> int:
        """Poll broker until we confirm how many shares actually filled."""
        deadline = time.time() + self.settings.order_fill_timeout
        while time.time() < deadline:
            o = self.client.get_order(order_id)
            if not o:
                break
            filled    = int(o.get("quantity_filled") or 0)
            remaining = int(o.get("quantity_remaining") or 0)
            status    = str(o.get("status") or "").upper()
            if filled > 0:
                return filled
            if remaining == 0 or status in {"TRANSACTED", "CANCELLED", "REJECTED"}:
                return filled
            time.sleep(0.05)
        return fallback

    def _market_chunk(self, u: Unwind, qty: int, side: ActionEnum) -> int:
        """Send one market order capped by remaining liability. Returns filled qty."""
        if qty <= 0:
            return 0
        try:
            o = self.client.place_order(u.ticker, OrderType.MARKET, qty, side)
        except RITError as e:
            print(f"    [error] mkt {side.value} {qty} {u.ticker}: {e}", flush=True)
            return 0

        raw_filled = int(o.get("quantity_filled") or 0)
        order_id   = o.get("order_id") or o.get("id")
        filled     = raw_filled

        if filled <= 0 and order_id is not None:
            filled = self._order_filled_qty(int(order_id), raw_filled)

        px = float(o.get("vwap") or o.get("price") or 0.0)
        if px <= 0 and order_id is not None:
            o2 = self.client.get_order(int(order_id)) or {}
            px = float(o2.get("vwap") or 0.0)
        if px <= 0:
            px = get_last_trade_price(self.client, u.ticker) \
                 or (get_mid_price(self.client, u.ticker) or 0.0)

        if filled > 0:
            u.fills_value += px * filled
            u.fills_qty   += filled
            print(f"    [mkt] {side.value} {filled}/{qty} {u.ticker} @ {px:.4f}", flush=True)
        return filled

    def market_to_target(self, u: Unwind, want: int, side: ActionEnum) -> None:
        """Send market orders until we've moved `want` shares OR liability is exhausted."""
        max_q     = self.settings.order_cap(u.ticker)
        remaining = want
        while remaining > 0:
            avail, avail_side = self.remaining_liability(u)
            if avail <= 0 or avail_side != side:
                return
            chunk  = min(remaining, max_q, avail)
            filled = self._market_chunk(u, chunk, side)
            if filled <= 0:
                return
            remaining -= filled

    def post_passive(self, u: Unwind, qty: int) -> None:
        """
        Post limit orders just inside the spread, but never more than
        (liability − already_resting) to avoid over-trading.
        → if selling: just below ask
        → if buying:  just above bid
        """
        if qty <= 0:
            return
        rem_qty, rem_side = self.remaining_liability(u)
        if rem_side != u.side:
            return

        avail = rem_qty - self.resting_qty(u.ticker, u.side)
        qty   = min(qty, max(0, avail))
        if qty <= 0:
            return

        bid, ask = self.best_bid_ask(u.ticker)

        if u.side == ActionEnum.SELL:
            px = (ask - self.settings.tick_size) if ask > 0 else (get_mid_price(self.client, u.ticker) or bid)
            if bid > 0:
                px = max(px, bid + self.settings.tick_size)   # don't cross the spread
        else:
            px = (bid + self.settings.tick_size) if bid > 0 else (get_mid_price(self.client, u.ticker) or ask)
            if ask > 0:
                px = min(px, ask - self.settings.tick_size)

        px    = max(self.settings.tick_size, round(px, 4))
        max_q = self.settings.order_cap(u.ticker)
        slice_q = max(1, int(max_q * self.settings.passive_slice_ratio))
        rem   = qty

        while rem > 0:
            chunk = min(rem, slice_q)
            try:
                self.client.place_order(u.ticker, OrderType.LIMIT, chunk, u.side, price=px)
                print(f"    [lim] {u.side.value} {chunk} {u.ticker} @ {px:.4f}", flush=True)
            except RITError as e:
                print(f"    [error] limit {u.side.value} {chunk} @ {px}: {e}", flush=True)
                return
            rem -= chunk


# =============================================================================
# 6. UNWIND LIFECYCLE
# =============================================================================
# start_unwind():
#   wait for position to update after tender accept
#   send 60% of qty as immediate market orders  ← get most of it done fast
#   post limit orders for remaining 40%         ← try to get better price
#
# pulse() (called every 0.5s):
#   if fully unwound → cancel leftover orders, done
#   if <60 ticks left → PANIC, send market orders for everything remaining
#   if 10 ticks since last reprice → cancel limits, repost at fresh price
#
# reconcile_to_baseline():
#   emergency cleanup → keep market-selling/buying until position = baseline
# =============================================================================

class UnwindManager(OrderPlacer):

    def _wait_for_position_delta(self, ticker: str, expected_delta: int, baseline: int) -> None:
        """Block until broker position reflects the tender fill (or we time out)."""
        deadline = time.time() + self.settings.accept_settle
        while time.time() < deadline:
            if self.position(ticker) == baseline + expected_delta:
                return
            time.sleep(0.1)

    def start_unwind(
        self,
        tender_id: int,
        ticker: str,
        qty: int,
        price: float,
        is_buy_tender: bool,
        baseline_pos: int,
    ) -> Unwind:
        """
        Kick off a new unwind after accepting a tender.
          1. Wait for position to update.
          2. Send 60% immediately as market orders.
          3. Post limit orders for the rest.
        """
        expected_delta = qty if is_buy_tender else -qty
        self._wait_for_position_delta(ticker, expected_delta, baseline_pos)

        side = ActionEnum.SELL if is_buy_tender else ActionEnum.BUY
        case = self.client.get_case()

        u = Unwind(
            tender_id=tender_id, ticker=ticker, qty=qty,
            tender_price=price, is_buy_tender=is_buy_tender, side=side,
            baseline_pos=baseline_pos,
            last_reprice_tick=int(case.get("tick", 0) or 0),
        )
        print(f"  [accept] tid={tender_id} {ticker} {qty} → unwind {side.value} to baseline={baseline_pos}", flush=True)

        # Step 1: send 60% as market orders right away
        self.cancel_all_and_wait(ticker)
        self.market_to_target(u, max(1, round(qty * self.settings.market_ratio)), side)

        # Step 2: post passive limits for whatever is left
        rem_qty, rem_side = self.remaining_liability(u)
        if rem_side == side:
            self.post_passive(u, rem_qty)

        return u

    def pulse(self, u: Unwind, case: dict[str, Any]) -> None:
        """
        Called every 0.5s while an unwind is active.
          - If done → cancel any leftover orders.
          - If <60 ticks left → PANIC, market everything.
          - Every 10 ticks → cancel limits and repost at fresh price.
        """
        rem, side = self.remaining_liability(u)

        # Done ✅ → tidy up
        if rem == 0:
            self.cancel_all_and_wait(u.ticker)
            return

        # PANIC → flatten immediately with market orders
        if self.ticks_remaining(case) < self.settings.urgency_ticks:
            print(f"  [urgency] flatten {rem} {u.ticker}", flush=True)
            self.cancel_all_and_wait(u.ticker)
            self.market_to_target(u, rem, side)
            return

        # Reprice → cancel and repost limits at current spread
        tick = int(case.get("tick", 0) or 0)
        if tick - u.last_reprice_tick < self.settings.reprice_every:
            return

        u.last_reprice_tick = tick
        self.cancel_all_and_wait(u.ticker)
        time.sleep(0.05)
        rem, rem_side = self.remaining_liability(u)
        if rem_side == u.side:
            self.post_passive(u, rem)

    def reconcile_to_baseline(self, u: Unwind, timeout: float = 5.0) -> None:
        """
        Emergency cleanup — keep sending market orders until
        position == baseline (or we time out).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            rem, side = self.remaining_liability(u)
            if rem == 0:
                return
            self.cancel_all_and_wait(u.ticker)
            rem, side = self.remaining_liability(u)
            if rem == 0:
                return
            chunk  = min(rem, self.settings.order_cap(u.ticker))
            filled = self._market_chunk(u, chunk, side)
            if filled <= 0:
                time.sleep(0.1)
                continue
            time.sleep(0.1)

    def _run_unwind_loop(self, u: Unwind) -> None:
        """Run pulse() until the unwind is complete or the session ends."""
        deadline   = time.monotonic() + 900
        zero_streak = 0

        while True:
            rem, _ = self.remaining_liability(u)
            if rem == 0:
                zero_streak += 1
                if zero_streak >= 2:
                    break
            else:
                zero_streak = 0

            case = self.client.get_case()
            if case.get("status") == "STOPPED" or time.monotonic() > deadline:
                self.reconcile_to_baseline(u, timeout=5.0)
                break

            self.pulse(u, case)
            time.sleep(self.settings.poll_interval)

        # Tidy up any resting orders before we hand back control
        self.reconcile_to_baseline(u, timeout=5.0)
        self.cancel_all_and_wait(u.ticker)


# =============================================================================
# 7. TENDER EVALUATION (the decision engine)
# =============================================================================
# evaluate(tender):
#   1. Is it expired?            → DECLINE
#   2. Can we get a mid price?   → DECLINE if not
#   3. Walk the order book to estimate slippage
#   4. expected_pnl = spread_captured - slippage - commission
#   5. Would this breach risk limits?      → DECLINE
#   6. Is the book deep enough?            → DECLINE
#   7. Is expected_pnl > 0?               → DECLINE if not
#   If all checks pass → ACCEPT → start_unwind() → run_unwind_loop()
#   Then calculate actual P&L and return the full record.
# =============================================================================

class TenderEvaluator(UnwindManager):

    @staticmethod
    def _walk_slip(levels, qty: int, ref_px: float, sell_into_bids: bool) -> tuple[float, bool]:
        """
        Walk the order book and estimate total slippage to fill `qty` shares.
        Returns (total_slippage, was_fully_covered).
        """
        rem  = qty
        slip = 0.0
        for lvl in levels:
            avail = int(lvl.quantity_remaining or lvl.quantity or 0)
            if avail <= 0:
                continue
            px   = float(lvl.price)
            take = min(rem, avail)
            slip += (ref_px - px) * take if sell_into_bids else (px - ref_px) * take
            rem  -= take
            if rem <= 0:
                return slip, True   # fully covered ✅
        return slip, False          # book too thin ❌

    def _risk_blocks(self, ticker: str, qty: int, is_buy: bool) -> bool:
        """Would accepting this tender breach our net or gross position limits?"""
        proj = self.all_positions()
        proj[ticker] = proj.get(ticker, 0) + (qty if is_buy else -qty)
        gross = sum(abs(v) for v in proj.values())
        net   = abs(sum(proj.values()))
        return gross > self.settings.gross_limit or net > self.settings.net_limit

    def _safe_decline(self, tid: int) -> None:
        try:
            self.client.decline_tender(tid)
        except RITError as e:
            print(f"  [warn] decline {tid}: {e}", flush=True)

    def evaluate(self, tender: Any, tick: int) -> TenderRecord | None:
        """
        Decide whether to accept or decline a tender offer.

        Checks (in order):
          1. Expired?
          2. No mid price?
          3. Book error?
          4. Empty book?
          5. Risk limits breached?
          6. Book too thin?
          7. Expected P&L <= 0?

        If all pass → accept, unwind, record P&L.
        """
        tid    = getattr(tender, "id", None)
        ticker = getattr(tender, "ticker", None)
        qty    = int(getattr(tender, "quantity", 0) or 0)
        price  = float(getattr(tender, "price", 0) or 0)
        action = str(getattr(tender, "action", "") or "").upper()
        expiry = int(getattr(tender, "expiry_tick", 0) or 0)

        if not (tid and ticker and qty and price and action):
            return None

        is_buy = "BUY" in action
        rec = TenderRecord(
            tick=tick, tender_id=int(tid), ticker=str(ticker),
            action=action, quantity=qty, tender_price=price,
        )

        # --- Check 1: expired? ---
        if expiry - tick <= 0:
            self._safe_decline(int(tid))
            rec.decision, rec.decline_reason = "DECLINED", "expired"
            return rec

        # --- Check 2: can we get a mid price? ---
        mid = get_mid_price(self.client, ticker)
        if not mid:
            self._safe_decline(int(tid))
            rec.decision, rec.decline_reason = "DECLINED", "no mid"
            return rec
        rec.mid_price   = mid
        rec.edge_signed = (mid - price) / mid
        rec.edge        = abs(rec.edge_signed)

        # --- Check 3: can we read the book? ---
        try:
            book = self.client.get_securities_book(ticker, limit=self.settings.book_depth)
        except RITError:
            self._safe_decline(int(tid))
            rec.decision, rec.decline_reason = "DECLINED", "book error"
            return rec

        # --- Check 4: is the book empty? ---
        bids      = sorted(book.bids, key=lambda x: float(x.price), reverse=True)
        asks      = sorted(book.asks, key=lambda x: float(x.price))
        side_book = bids if is_buy else asks
        if not side_book:
            self._safe_decline(int(tid))
            rec.decision, rec.decline_reason = "DECLINED", "empty book"
            return rec

        ref_px          = float(side_book[0].price)
        slip, covered   = self._walk_slip(side_book, qty, ref_px, sell_into_bids=is_buy)

        rec.estimated_slippage = slip if covered else float("inf")
        rec.spread_captured    = abs(ref_px - price) * qty
        rec.txn_cost           = qty * self.settings.commission
        rec.expected_pnl       = rec.spread_captured - rec.estimated_slippage - rec.txn_cost
        rec.risk_blocked       = self._risk_blocks(ticker, qty, is_buy)

        # --- Checks 5, 6, 7: risk / depth / profitability ---
        reason = None
        if rec.risk_blocked:
            reason = "risk"
        elif not covered:
            reason = "depth insufficient"
        elif rec.expected_pnl <= 0:
            reason = "E[pnl]<=0"

        if reason:
            self._safe_decline(int(tid))
            rec.decision, rec.decline_reason = "DECLINED", reason
            return rec

        # --- All checks passed → ACCEPT ---
        try:
            baseline_before = self.position(str(ticker))
            self.client.accept_tender(int(tid))
        except (RITError, RuntimeError) as e:
            rec.decision, rec.decline_reason = "DECLINED", f"accept_failed:{e}"
            return rec

        rec.decision = "ACCEPTED"

        # Unwind and wait for it to complete
        u = self.start_unwind(int(tid), str(ticker), qty, price, is_buy, baseline_before)
        self._run_unwind_loop(u)

        # --- Calculate actual P&L ---
        avg_unwind             = (u.fills_value / u.fills_qty) if u.fills_qty else 0.0
        filled                 = min(u.fills_qty, qty)
        rec.gross_pnl          = (avg_unwind - price) * filled if is_buy else (price - avg_unwind) * filled
        over                   = max(0, u.fills_qty - qty)
        rec.commission_cost    = u.fills_qty * self.settings.commission
        rec.liability_violation_volume = over
        rec.liability_fine     = over * self.settings.liab_fine
        rec.avg_unwind_price   = round(avg_unwind, 4)
        rec.gross_pnl          = round(rec.gross_pnl, 4)
        rec.net_pnl            = round(rec.gross_pnl - rec.commission_cost - rec.liability_fine, 4)
        rec.residual           = abs(self.position(str(ticker)) - u.baseline_pos)

        return rec
