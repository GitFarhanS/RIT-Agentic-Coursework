"""Top-level deliberative agent session loop."""

from __future__ import annotations

import time
from typing import Any

from coursework.agents.deliberative.pipeline import TenderEvaluator
from coursework.agents.deliberative.records import SessionStats, Unwind
from coursework.agents.deliberative.settings import DeliberativeSettings
from coursework.infrastructure.rotman_client import RotmanSDK


class DeliberativeAgent(TenderEvaluator):
    """
    Top-level agent. Inherits the full stack:
    BrokerHelpers → LiabilityChecker → OrderPlacer
    → UnwindManager → TenderEvaluator → DeliberativeAgent
    """

    def __init__(self, client: RotmanSDK, settings: DeliberativeSettings | None = None) -> None:
        super().__init__(client, settings or DeliberativeSettings())
        self._active: Unwind | None = None

    def run_session(self, n: int) -> SessionStats:
        """
        Main loop for one game session.
        Priority order every tick:
          1. Service the active unwind (if any).
          2. Check for new tenders (only when no active unwind).
          3. Exit cleanly at end of period.
        """
        st = SessionStats(session=n)
        seen: set[int] = set()
        print(f"\n[session {n}] starting", flush=True)

        while True:
            case = self.client.get_case()

            if case.get("status") == "STOPPED":
                break

            tick = int(case.get("tick", 0) or 0)

            if self._active is not None:
                self.pulse(self._active, case)
                rem, _ = self.remaining_liability(self._active)
                if rem == 0:
                    self._active = None

            if self._active is None:
                try:
                    tenders = self.client.get_tenders() or []
                except Exception:
                    tenders = []

                for t in tenders:
                    tid = getattr(t, "id", None)
                    if tid is None or tid in seen:
                        continue
                    seen.add(tid)
                    st.seen += 1

                    rec = self.evaluate(t, tick)
                    if rec is None:
                        continue
                    st.records.append(rec)

                    if rec.decision == "ACCEPTED":
                        st.accepted += 1
                        st.total_gross_pnl += rec.gross_pnl
                        st.total_net_pnl += rec.net_pnl
                    else:
                        st.declined += 1

            if tick >= 299:
                if self._active is not None:
                    self.reconcile_to_baseline(self._active, timeout=5.0)
                    self.cancel_all_and_wait(self._active.ticker)
                    self._active = None
                break

            time.sleep(self.settings.poll_interval)

        return st
