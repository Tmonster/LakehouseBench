"""
Plot the analytical benchmark: per-query latency by engine.

Reads the append-only record files (results/logs.csv + results/time.csv), and for
each engine plots ONLY its most recent analytical run. Bars show the median query
time across that run's repetitions, with min/max whiskers.

Other benchmark types (power, throughput, composite) have their own plotting
scripts — this one is analytical-only.

Usage:
    uv run --extra plot python plot_analytical_results.py
    uv run --extra plot python plot_analytical_results.py --results-dir results
    uv run --extra plot python plot_analytical_results.py --output analytical.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

BENCHMARK = "analytical"
DEFAULT_OUTPUT_DIR = Path("results/images/tmp")


def latest_analytical_runs(logs: pd.DataFrame) -> pd.DataFrame:
    """One row per engine: the most recent analytical run."""
    analytical = logs[logs["benchmark"] == BENCHMARK].copy()
    if analytical.empty:
        return analytical
    analytical["benchmark_start_time"] = pd.to_datetime(analytical["benchmark_start_time"])
    # Sort ascending, keep the last (most recent) row per engine.
    return analytical.sort_values("benchmark_start_time").groupby("engine", as_index=False).tail(1)


def load(results_dir: Path) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    time_path = results_dir / "time.csv"
    for p in (logs_path, time_path):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            sys.exit(1)

    logs = pd.read_csv(logs_path)
    runs = latest_analytical_runs(logs)
    if runs.empty:
        return runs

    run_ids = set(runs["run_id"])
    time = pd.read_csv(time_path)
    df = time[(time["run_id"].isin(run_ids)) & (time["benchmark"] == BENCHMARK)].copy()

    # Per-query elapsed comes from the wall-clock start/end timestamps.
    df["query_start_time"] = pd.to_datetime(df["query_start_time"])
    df["query_end_time"] = pd.to_datetime(df["query_end_time"])
    df["elapsed_seconds"] = (df["query_end_time"] - df["query_start_time"]).dt.total_seconds()

    # Drop failed queries — they have no meaningful latency.
    df = df[df["error"].isna()]
    df["label"] = df["engine"] + " sf" + df["scale_factor"].astype(str)
    return df


def plot(df: pd.DataFrame, output: Path | None) -> None:
    labels = sorted(df["label"].unique())
    queries = sorted(df["query"].unique())

    sns.set_theme(style="whitegrid", font_scale=0.9)
    palette = sns.color_palette("tab10", n_colors=len(labels))

    fig, ax = plt.subplots(figsize=(10, 4))
    sns.barplot(
        data=df,
        x="query",
        y="elapsed_seconds",
        hue="label",
        hue_order=labels,
        order=queries,
        estimator="median",
        errorbar=("pi", 100),   # min/max whiskers across runs
        palette=palette,
        width=0.9,
        ax=ax,
    )

    ax.set_title("Analytical query latency by engine (most recent run, median, min/max across runs)")
    ax.set_xlabel("Query")
    ax.set_ylabel("Elapsed (s)")
    ax.legend(title="Engine", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout(rect=[0, 0, 0.82, 1])

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150)
        print(f"Saved to {output}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="Directory holding logs.csv and time.csv (default: results)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/analytical.png)")
    args = parser.parse_args()

    df = load(args.results_dir)
    if df.empty:
        print("No analytical results found.", file=sys.stderr)
        sys.exit(1)

    output = args.output or (DEFAULT_OUTPUT_DIR / "analytical.png")
    plot(df, output)


if __name__ == "__main__":
    main()
