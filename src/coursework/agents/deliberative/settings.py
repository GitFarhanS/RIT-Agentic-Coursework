from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


def _default_output_dir() -> Path:
    """Under ``coursework/agents/agent_logs`` (next to reactive / GA entrypoints)."""
    return Path(__file__).resolve().parents[1] / "agent_logs"


@dataclass(frozen=True)
class DeliberativeSettings:
    """Tunable parameters for the deliberative tender + unwind pipeline."""

    poll_interval: float = 0.5
    max_sessions: int | None = 3
    stopped_timeout: float = 700.0
    output_dir: Path = field(default_factory=_default_output_dir)
    read_retries: int = 3
    read_retry_sleep: float = 0.05
    order_fill_timeout: float = 1.5
    max_crzy: int = 25_000
    max_tame: int = 10_000
    default_chunk: int = 10_000
    book_depth: int = 20
    market_ratio: float = 0.6
    urgency_ticks: int = 60
    reprice_every: int = 10
    passive_slice_ratio: float = 1.0
    net_limit: int = 100_000
    gross_limit: int = 250_000
    commission: float = 0.02
    liab_fine: float = 0.39
    tick_size: float = 0.01
    accept_settle: float = 3.0

    def order_cap(self, ticker: str) -> int:
        if ticker == "CRZY":
            return self.max_crzy
        if ticker == "TAME":
            return self.max_tame
        return self.default_chunk

    @classmethod
    def from_ga_params(cls, params: dict[str, Any]) -> DeliberativeSettings:
        """Build settings from ``ga/evolved_params.json`` ``params`` object."""
        base = cls()
        return replace(
            base,
            market_ratio=float(params["market_order_ratio"]),
            urgency_ticks=int(300 * float(params["time_decay_factor"]) / 3.0),
            passive_slice_ratio=float(params["slice_size_ratio"]),
        )
