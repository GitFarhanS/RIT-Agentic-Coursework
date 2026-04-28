#!/usr/bin/env python3
"""Entry point for the genetic algorithm (avoids loading ga.genetic_algorithm via ga/__init__.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ga.genetic_algorithm import run

if __name__ == "__main__":
    run()
