from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    api_key: str
    host: str


def load_runtime_config() -> RuntimeConfig:
    """
    Load shared runtime config for Rotman agents.

    Uses environment variables first, then falls back to existing local defaults
    so current behavior is preserved for coursework runs.
    """
    api_key = os.environ.get("ROTMAN_API_KEY", "TPIAOJIF")
    host = os.environ.get("ROTMAN_HOST", "http://192.168.64.9:9999/v1").rstrip("/")
    return RuntimeConfig(api_key=api_key, host=host)
