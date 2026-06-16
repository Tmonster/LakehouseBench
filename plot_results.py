"""
Plot the analytical benchmark: per-query latency by engine.

Reads the append-only record files (results/logs.csv + results/time.csv), and for
each engine plots ONLY its most recent run of the selected benchmark. Bars show the
median query time across that run's repetitions, with min/max whiskers.

Use --sf / --instance to scope to a single scale factor and machine so the
comparison is apples-to-apples; the script warns if the selected runs still span
more than one scale factor or instance type.

Defaults to the analytical benchmark; other benchmark types have their own per-query
semantics, but --benchmark lets you point this at them.

Usage:
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge
    uv run --extra plot python plot_results.py            # all runs, latest per engine
    uv run --extra plot python plot_results.py --output analytical.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

DEFAULT_OUTPUT_DIR = Path("results/images/tmp")


def select_latest_runs(
    logs: pd.DataFrame, benchmark: str, sf: int | None, instance: str | None,
) -> pd.DataFrame:
    """One row per engine: the most recent matching run after applying filters."""
    sel = logs[logs["benchmark"] == benchmark].copy()
    if sf is not None:
        sel = sel[sel["scale_factor"] == sf]
    if instance is not None:
        sel = sel[sel["bench_instance_type"] == instance]
    if sel.empty:
        return sel

    sel["benchmark_start_time"] = pd.to_datetime(sel["benchmark_start_time"])
    runs = sel.sort_values("benchmark_start_time").groupby("engine", as_index=False).tail(1)

    # Guard against silently comparing across environments / scale factors.
    if runs["scale_factor"].nunique() > 1:
        sfs = ", ".join(str(s) for s in sorted(runs["scale_factor"].unique()))
        print(f"warning: selected runs span multiple scale factors ({sfs}); "
              f"pass --sf to pin one.", file=sys.stderr)
    if runs["bench_instance_type"].nunique() > 1:
        insts = ", ".join(sorted(runs["bench_instance_type"].dropna().unique()))
        print(f"warning: selected runs span multiple instance types ({insts}); "
              f"pass --instance to pin one.", file=sys.stderr)
    return runs


def load(
    results_dir: Path, benchmark: str, sf: int | None, instance: str | None,
) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    time_path = results_dir / "time.csv"
    for p in (logs_path, time_path):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            sys.exit(1)

    logs = pd.read_csv(logs_path)
    runs = select_latest_runs(logs, benchmark, sf, instance)
    if runs.empty:
        return runs

    run_ids = set(runs["run_id"])
    time = pd.read_csv(time_path)
    df = time[(time["run_id"].isin(run_ids)) & (time["benchmark"] == benchmark)].copy()

    # Per-query elapsed comes from the wall-clock start/end timestamps.
    df["query_start_time"] = pd.to_datetime(df["query_start_time"])
    df["query_end_time"] = pd.to_datetime(df["query_end_time"])
    df["elapsed_seconds"] = (df["query_end_time"] - df["query_start_time"]).dt.total_seconds()

    # Drop failed queries — they have no meaningful latency.
    df = df[df["error"].isna()]
    df["label"] = df["engine"] + " sf" + df["scale_factor"].astype(str)
    return df


def plot(df: pd.DataFrame, benchmark: str, output: Path | None) -> None:
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

    sfs = "/".join(f"sf{s}" for s in sorted(df["scale_factor"].unique()))
    instances = ", ".join(sorted(df["bench_instance_type"].dropna().unique())) or "unknown"
    ax.set_title(f"{benchmark.capitalize()} query latency by engine — {sfs} on {instances}\n"
                 f"(most recent run per engine, median, min/max across runs)")
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
    parser.add_argument("--benchmark", default="analytical",
                        help="Benchmark to plot (default: analytical)")
    parser.add_argument("--sf", type=int, default=None, help="Filter to one scale factor")
    parser.add_argument("--instance", default=None, help="Filter to one bench_instance_type")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/<benchmark>.png)")
    args = parser.parse_args()

    df = load(args.results_dir, args.benchmark, args.sf, args.instance)
    if df.empty:
        print(f"No {args.benchmark} results found for the given filters.", file=sys.stderr)
        sys.exit(1)

    output = args.output or (DEFAULT_OUTPUT_DIR / f"{args.benchmark}.png")
    plot(df, args.benchmark, output)


if __name__ == "__main__":
    main()
