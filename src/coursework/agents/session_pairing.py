r"""
Pair reactive and GA runs on the same RIT stochastic path (optional lab workflow).

RIT advances ``period`` every time a session completes; a GA session run *after*
many reactive sessions therefore sees a different tender/book stream than
``r_k`` even when file names suggest pairing. Coursework summary statistics
(``scripts/summarize_live_results.py``) treat reactive and GA CSVs as
*independent* batches unless you complete this replay workflow.

Workflow
--------
1. Run the reactive agent with ``--save-anchors DIR``. At each session start
   (ACTIVE, tick 0), a fingerprint is written to ``DIR/r_<session>.json``.
2. **Restart the same case in the RIT client** so the simulator draws the same
   scenario from tick 0 again (same case file / instructor procedure as for the
   reactive run).
3. Run the GA agent with ``--match-anchors DIR``. Before each session it waits
   until tick 0 matches the saved fingerprint for that session index.

Matching uses case name plus top-of-book mids for CRZY and TAME (when present).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coursework.agents.common import wait_for_session_start
from coursework.infrastructure.rotman_client import RotmanSDK


@dataclass(frozen=True)
class ScenarioFingerprint:
    """Tick-0 market snapshot for pairing runs."""

    case_name: str
    period: int
    tick: int
    mids: dict[str, float | None]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "period": self.period,
            "tick": self.tick,
            "mids": self.mids,
        }

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> ScenarioFingerprint:
        raw = d.get("mids") or {}
        mids: dict[str, float | None] = {}
        for k, v in raw.items():
            mids[str(k)] = float(v) if v is not None else None
        return cls(
            case_name=str(d.get("case_name", "")),
            period=int(d.get("period", 0) or 0),
            tick=int(d.get("tick", 0) or 0),
            mids=mids,
        )


def capture_fingerprint(client: RotmanSDK, tickers: tuple[str, ...] = ("CRZY", "TAME")) -> ScenarioFingerprint:
    case = client.get_case() or {}
    mids: dict[str, float | None] = {}
    for t in tickers:
        try:
            book = client.get_securities_book(ticker=t, limit=1)
        except Exception:
            mids[t] = None
            continue
        bb = book.bids[0].price if book.bids else None
        ba = book.asks[0].price if book.asks else None
        if bb is not None and ba is not None:
            mids[t] = (float(bb) + float(ba)) / 2.0
        else:
            mids[t] = None
    return ScenarioFingerprint(
        case_name=str(case.get("name", "") or ""),
        period=int(case.get("period", 0) or 0),
        tick=int(case.get("tick", 0) or 0),
        mids=mids,
    )


def capture_fingerprint_at_tick_zero(
    client: RotmanSDK,
    *,
    settle_sec: float = 2.0,
    tickers: tuple[str, ...] = ("CRZY", "TAME"),
) -> ScenarioFingerprint:
    """Sample repeatedly while ACTIVE at tick 0; reduces race after session-start wait."""
    deadline = time.monotonic() + float(settle_sec)
    last: ScenarioFingerprint | None = None
    while time.monotonic() < deadline:
        case = client.get_case() or {}
        if case.get("status") == "ACTIVE" and int(case.get("tick", 0) or 0) == 0:
            last = capture_fingerprint(client, tickers=tickers)
        time.sleep(0.05)
    return last if last is not None else capture_fingerprint(client, tickers=tickers)


def fingerprints_match(
    a: ScenarioFingerprint,
    b: ScenarioFingerprint,
    *,
    mid_eps: float = 0.02,
    require_case_name: bool = True,
) -> bool:
    if require_case_name and (a.case_name or "") != (b.case_name or ""):
        return False
    keys = set(a.mids) | set(b.mids)
    for k in keys:
        ma, mb = a.mids.get(k), b.mids.get(k)
        if ma is None or mb is None:
            if ma != mb:
                return False
            continue
        if abs(float(ma) - float(mb)) > mid_eps:
            return False
    return True


def save_fingerprint(path: Path, fp: ScenarioFingerprint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp.to_json_dict(), indent=2), encoding="utf-8")


def load_fingerprint(path: Path) -> ScenarioFingerprint:
    return ScenarioFingerprint.from_json_dict(json.loads(path.read_text(encoding="utf-8")))


def _wait_until_stopped(client: RotmanSDK, deadline: float) -> bool:
    while time.monotonic() < deadline:
        case = client.get_case() or {}
        if case.get("status") == "STOPPED":
            return True
        time.sleep(0.5)
    return False


def wait_for_matched_session_start(
    client: RotmanSDK,
    target: ScenarioFingerprint,
    *,
    stopped_wait_timeout: float,
    match_timeout_sec: float = 1200.0,
    mid_eps: float = 0.02,
) -> bool:
    """
    Wait until we see ACTIVE tick 0 whose fingerprint matches ``target``.

    If a session starts but the fingerprint does not match (wrong restart or
    wrong case file), prints guidance and waits for STOPPED so the operator can
    restart the case and try again.
    """
    deadline = time.monotonic() + float(match_timeout_sec)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        remaining = max(5.0, deadline - time.monotonic())
        twait = min(float(stopped_wait_timeout), remaining)
        if not wait_for_session_start(client=client, stopped_wait_timeout=twait):
            print(
                f"[pairing] attempt {attempt}: wait_for_session_start timed out; retrying...",
                flush=True,
            )
            continue
        snap = capture_fingerprint_at_tick_zero(client)
        if fingerprints_match(snap, target, mid_eps=mid_eps):
            print(f"[pairing] scenario matched reactive anchor (attempt {attempt}).", flush=True)
            return True
        print(
            "[pairing] NEW session at tick 0 does NOT match saved reactive anchor.\n"
            "  -> In RIT: stop the case if it is running, then **restart the same case file**\n"
            "     so tick 0 mids match the reactive run for this session index.\n"
            f"  Saved anchor mids: {target.mids!r}\n"
            f"  Current mids:      {snap.mids!r}\n"
            "  Waiting for STOPPED before retrying...",
            flush=True,
        )
        _wait_until_stopped(client, deadline)

    print("[pairing] Timed out waiting for a matching scenario restart.", flush=True)
    return False
