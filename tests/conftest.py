"""
Shared pytest configuration and fixtures for live RIT tests.
Loads API key from .env (project root); adds project root to sys.path; provides live_client.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Prefer src layout (``coursework`` package); keep root + agents for legacy shims.
_root = Path(__file__).resolve().parent.parent
_src = _root / "src"
_agents = _root / "agents"
for _p in (_src, _root, _agents):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


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

from sdk import RotmanSDK


def _get_live_client():
    """Create SDK client from env (RIT_API_KEY from .env or env). Returns None if not set."""
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
