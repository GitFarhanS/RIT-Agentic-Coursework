# RIT-Agentic-Coursework

Coursework for **Designing Intelligent Agents** (University of Nottingham): autonomous **liability-trading** agents for the Rotman Interactive Trader (RIT) **LT3** case. The project compares a **reactive** spread-threshold baseline to a **genetic-algorithm (GA)** tuned **depth-aware deliberative** policy (same deliberation core, four scalar parameters evolved offline on a synthetic environment calibrated from logged sessions).

## What is in this repository

| Area | Role |
|------|------|
| `src/coursework/` | Installable package: agents, RIT client, runtime config |
| `src/coursework/agents/reactive_agent.py` | Top-of-book reflex policy and live session loop |
| `src/coursework/agents/deliberative/` | Shared book-walking slippage estimate, tender EPL, mixed limit–market unwind |
| `src/coursework/agents/ga_agent.py` | GA parameter injection and live harness |
| `ga/` | Tournament GA (`genetic_algorithm.py`), fitness, evolved parameters (`evolved_params.json`) |
| `synthetic/` | Offline session generator driven by fitted parameters |
| `data/` | Aggregated logs, `synthetic_model_params.json` (source of truth for the generator) |
| `scripts/` | Aggregation, distribution fitting, monitors, etc. |
| `tests/` | Pytest suite (RIT integration tests where configured) |

## Requirements

- **Python 3.13+** (see `pyproject.toml`)
- Network access to your RIT instance when running live agents

## Setup

From the repository root:

```bash
pip install -e .
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

## Configuration

Live agents use `src/coursework/config/runtime.py`. Override defaults with:

- `ROTMAN_API_KEY` — API key for the RIT REST API
- `ROTMAN_HOST` — Base URL for the API (a trailing slash is stripped automatically)

A `.env` file is convenient locally; it is listed in `.gitignore` and is not committed.

## Running agents

After an editable install, from the repo root:

```bash
python -m coursework.agents.reactive_agent
python -m coursework.agents.ga_agent
```

Each agent writes session logs (e.g. Excel under the agent's output directory). Use `--help` on each module for CLI options (sessions, pairing/anchors, paths).

## Offline GA

The genetic algorithm lives under `ga/`. Typical entry point:

```bash
python ga/genetic_algorithm.py
```

Fitness uses the synthetic environment; pin `data/synthetic_model_params.json` when comparing runs.

## Tests

```bash
pytest
```
