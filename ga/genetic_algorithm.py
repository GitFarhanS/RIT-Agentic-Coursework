"""
Hand-rolled tournament genetic algorithm for real-valued parameters.
Uses ga.fitness.evaluate; fully reproducible via a single numpy Generator (no global RNG).

Run from repository root: ``python -m ga.genetic_algorithm``
"""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from .fitness import DEFAULT_MODEL, evaluate

PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "threshold": (0.001, 0.05),
    "market_order_ratio": (0.0, 1.0),
    "time_decay_factor": (0.5, 3.0),
    "slice_size_ratio": (0.2, 1.0),
}
GENE_ORDER: tuple[str, ...] = tuple(PARAM_BOUNDS.keys())
TOURNAMENT_SIZE = 3
OUTPUT_JSON = Path(__file__).resolve().parent / "evolved_params.json"


def _clip_individual(ind: dict[str, float]) -> dict[str, float]:
    return {k: float(np.clip(ind[k], lo, hi)) for k, (lo, hi) in PARAM_BOUNDS.items()}


def _random_individual(rng: np.random.Generator) -> dict[str, float]:
    ind = {k: float(rng.uniform(lo, hi)) for k, (lo, hi) in PARAM_BOUNDS.items()}
    return _clip_individual(ind)


def _uniform_crossover(
    a: dict[str, float], b: dict[str, float], rng: np.random.Generator
) -> dict[str, float]:
    child = {}
    for k in GENE_ORDER:
        child[k] = a[k] if rng.random() < 0.5 else b[k]
    return _clip_individual(child)


def _mutate(ind: dict[str, float], rng: np.random.Generator) -> dict[str, float]:
    out = dict(ind)
    for k in GENE_ORDER:
        if rng.random() < 0.2:
            lo, hi = PARAM_BOUNDS[k]
            sigma = 0.1 * (hi - lo)
            out[k] = float(out[k] + rng.normal(0.0, sigma))
    return _clip_individual(out)


def _resolve_markdown_log_path(markdown_log: Path | str | bool | None) -> Path | None:
    if markdown_log is False:
        return None
    if markdown_log in (None, True):
        return (
            OUTPUT_JSON.parent
            / f"ga_training_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
        )
    return Path(markdown_log)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    out = ["| " + " | ".join(headers) + " |", sep]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out) + "\n"


def _write_generation_md(
    f: TextIO,
    gen: int,
    generations: int,
    eval_rows: list[tuple[int, float, dict[str, float]]],
    ranked_pop: list[dict[str, float]],
    ranked_fit: list[float],
    gen_elapsed: float,
    best_fitness_ever: float,
) -> None:
    g = gen + 1
    f.write(f"\n## Generation {g} / {generations}\n\n")
    f.write("### Evaluations (chronological)\n\n")
    headers = ["eval#", "fitness"] + list(GENE_ORDER)
    erows: list[list[str]] = []
    for n, fit, ind in eval_rows:
        erows.append(
            [str(n), f"{fit:+.6f}"] + [f"{ind[k]:.5f}" for k in GENE_ORDER]
        )
    f.write(_md_table(headers, erows))
    f.write("\n### Population ranked by fitness\n\n")
    rrows: list[list[str]] = []
    for rank, (fit, ind) in enumerate(zip(ranked_fit, ranked_pop), start=1):
        rrows.append(
            [str(rank), f"{fit:+.6f}"] + [f"{ind[k]:.5f}" for k in GENE_ORDER]
        )
    f.write(_md_table(["rank", "fitness"] + list(GENE_ORDER), rrows))
    f.write(
        f"\n**Generation time:** {gen_elapsed:.1f}s  \n"
        f"**Best fitness so far:** {best_fitness_ever:+.6f}\n"
    )


def _tournament_select(
    population: list[dict[str, float]],
    fitnesses: list[float],
    rng: np.random.Generator,
    size: int = TOURNAMENT_SIZE,
) -> dict[str, float]:
    n = len(population)
    k = min(size, n)
    idx = rng.choice(n, size=k, replace=False)
    best_j = int(idx[0])
    best_f = fitnesses[best_j]
    for j in idx[1:]:
        j = int(j)
        if fitnesses[j] > best_f:
            best_f = fitnesses[j]
            best_j = j
    return copy.deepcopy(population[best_j])


def run(
    model_path: Path | str | None = None,
    generations: int = 20,
    pop_size: int = 30,
    base_seed: int = 42,
    verbose: bool = True,
    markdown_log: Path | str | bool | None = None,
) -> dict[str, float]:
    """
    Run the GA; print progress; write ga/evolved_params.json.
    If ``markdown_log`` is not ``False``, append a human-readable log each generation
    (default: timestamped ``ga/ga_training_<UTC>.md``).
    Returns the best parameter dict (float values only).
    """
    mp = model_path or DEFAULT_MODEL
    rng = np.random.default_rng(base_seed)
    md_path = _resolve_markdown_log_path(markdown_log)
    md_file: TextIO | None = None

    population = [_random_individual(rng) for _ in range(pop_size)]
    best_fitness_ever = float("-inf")
    best_params_ever: dict[str, float] | None = None
    run_start = time.perf_counter()

    if verbose:
        print(
            "Starting GA "
            f"(generations={generations}, pop_size={pop_size}, "
            f"sessions_per_eval=5, base_seed={base_seed})"
        )
        print(f"Using model: {mp}")
        if md_path is not None:
            print(f"Markdown log: {md_path}")

    try:
        if md_path is not None:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_file = open(md_path, "w", encoding="utf-8")
            md_file.write("# GA training log\n\n")
            md_file.write(
                f"- **Started (UTC):** {datetime.now(timezone.utc).isoformat()}\n"
                f"- **Model:** `{mp}`\n"
                f"- **Generations:** {generations}\n"
                f"- **Population size:** {pop_size}\n"
                f"- **Sessions per fitness eval:** 5\n"
                f"- **Base seed:** {base_seed}\n\n"
            )
            md_file.flush()

        for gen in range(generations):
            gen_start = time.perf_counter()
            if verbose:
                print(f"\n[Generation {gen + 1}/{generations}] evaluating population...")
            fitnesses: list[float] = []
            eval_rows: list[tuple[int, float, dict[str, float]]] = []
            for i, ind in enumerate(population):
                fit = evaluate(
                    ind,
                    model_path=mp,
                    n_sessions=5,
                    base_seed=gen * 100 + i,
                )
                fitnesses.append(fit)
                eval_rows.append((i + 1, fit, copy.deepcopy(ind)))
                if verbose:
                    elapsed = time.perf_counter() - gen_start
                    avg_eval_s = elapsed / (i + 1)
                    remaining = pop_size - (i + 1)
                    eta_s = remaining * avg_eval_s
                    print(
                        f"  - eval {i + 1:2d}/{pop_size}: fitness={fit:+.6f} "
                        f"(elapsed={elapsed:.1f}s, eta={eta_s:.1f}s)"
                    )

            order = sorted(range(pop_size), key=lambda j: fitnesses[j], reverse=True)
            population = [population[j] for j in order]
            fitnesses = [fitnesses[j] for j in order]

            gen_best_f = fitnesses[0]
            gen_best = population[0]
            if gen_best_f > best_fitness_ever:
                best_fitness_ever = gen_best_f
                best_params_ever = copy.deepcopy(gen_best)

            gen_elapsed = time.perf_counter() - gen_start
            if verbose:
                print(
                    f"[Generation {gen + 1}/{generations}] done in {gen_elapsed:.1f}s | "
                    f"best_fitness={gen_best_f:+.6f} | "
                    f"params={{{', '.join(f'{k}={gen_best[k]:.5f}' for k in GENE_ORDER)}}}"
                )

            if md_file is not None:
                _write_generation_md(
                    md_file,
                    gen,
                    generations,
                    eval_rows,
                    population,
                    fitnesses,
                    gen_elapsed,
                    best_fitness_ever,
                )
                md_file.flush()

            if gen == generations - 1:
                break

            elite = copy.deepcopy(population[0])
            new_population: list[dict[str, float]] = [elite]
            while len(new_population) < pop_size:
                p1 = _tournament_select(population, fitnesses, rng)
                p2 = _tournament_select(population, fitnesses, rng)
                child = _uniform_crossover(p1, p2, rng)
                child = _mutate(child, rng)
                new_population.append(child)
            population = new_population
    finally:
        if md_file is not None:
            md_file.close()

    assert best_params_ever is not None

    if md_path is not None:
        with open(md_path, "a", encoding="utf-8") as mf:
            mf.write("\n---\n\n## Result\n\n")
            mf.write(f"- **Best fitness:** {best_fitness_ever:+.6f}\n")
            mf.write("- **Best parameters:**\n\n")
            for k in GENE_ORDER:
                mf.write(f"  - `{k}`: {best_params_ever[k]:.6f}\n")
            mf.write(
                f"\n- **Total wall time:** {time.perf_counter() - run_start:.1f}s\n"
                f"- **Finished (UTC):** {datetime.now(timezone.utc).isoformat()}\n"
            )
        if verbose:
            print(f"Wrote markdown log: {md_path}")

    payload: dict[str, Any] = {
        "params": {k: float(best_params_ever[k]) for k in GENE_ORDER},
        "fitness": float(best_fitness_ever),
        "generations": int(generations),
        "population_size": int(pop_size),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    if verbose:
        total_elapsed = time.perf_counter() - run_start
        print(f"\nFinished GA in {total_elapsed:.1f}s")
        print(f"Wrote {OUTPUT_JSON}")

    return {k: float(best_params_ever[k]) for k in GENE_ORDER}


if __name__ == "__main__":
    run()
