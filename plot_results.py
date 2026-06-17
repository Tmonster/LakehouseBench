"""
Plot the analytical benchmark: per-query latency by engine.

Reads the append-only record files (results/logs.csv + results/time.csv), and plots
the most recent run per (engine, engine_version, table_format) for the selected
benchmark — so different engine versions and table formats (iceberg / ducklake / delta)
each show as separate series. Bars show the median query time across that run's
repetitions, with min/max whiskers.

Use --sf / --instance / --storage to scope the comparison; the script warns if the
selected runs still span more than one scale factor or instance type.
--storage takes 'remote', 'local', or a specific service (s3/gcs/azure).

Defaults to the analytical benchmark; other benchmark types have their own per-query
semantics, but --benchmark lets you point this at them.

Usage:
    uv run --extra plot python plot_results.py --sf 10 --storage remote
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge
    uv run --extra plot python plot_results.py --run-ids 1a2b3c 4d5e6f   # exact runs
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
    storage: str | None, engine: str | None, table_format: str | None,
) -> pd.DataFrame:
    """
    One row per (engine, engine_version, table_format): the most recent matching run
    after filters — so different engine versions and table formats each appear as their
    own series rather than collapsing together.
    """
    sel = logs[logs["benchmark"] == benchmark].copy()
    if sf is not None:
        sel = sel[sel["scale_factor"] == sf]
    if instance is not None:
        sel = sel[sel["bench_instance_type"] == instance]
    if engine is not None:
        sel = sel[sel["engine"] == engine]
    if table_format is not None:
        sel = sel[sel["table_format"] == table_format]
    if storage is not None:
        if storage == "remote":
            sel = sel[sel["storage_service"] != "local"]
        elif storage == "local":
            sel = sel[sel["storage_service"] == "local"]
        else:  # a specific service, e.g. s3 / gcs / azure
            sel = sel[sel["storage_service"] == storage]
    if sel.empty:
        return sel

    sel["table_format"] = sel["table_format"].fillna("unknown")
    sel["engine_version"] = sel["engine_version"].fillna("unknown")
    sel["benchmark_start_time"] = pd.to_datetime(sel["benchmark_start_time"])
    runs = (sel.sort_values("benchmark_start_time")
               .groupby(["engine", "engine_version", "table_format"], as_index=False).tail(1))

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


def select_run_ids(logs: pd.DataFrame, run_ids: list[str]) -> pd.DataFrame:
    """Exactly the given run_ids — bypasses the latest-per-engine/format selection."""
    runs = logs[logs["run_id"].isin(run_ids)].copy()
    missing = [r for r in run_ids if r not in set(runs["run_id"])]
    if missing:
        print(f"warning: run_id(s) not found in logs.csv: {', '.join(missing)}", file=sys.stderr)
    if runs.empty:
        return runs
    runs["table_format"] = runs["table_format"].fillna("unknown")
    runs["benchmark_start_time"] = pd.to_datetime(runs["benchmark_start_time"])
    return runs


def load(
    results_dir: Path, benchmark: str, sf: int | None, instance: str | None,
    storage: str | None, engine: str | None, table_format: str | None,
    run_ids: list[str] | None,
) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    time_path = results_dir / "time.csv"
    for p in (logs_path, time_path):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            sys.exit(1)

    logs = pd.read_csv(logs_path)
    # Explicit --run-ids take precedence over the latest-per-engine/format selection.
    runs = select_run_ids(logs, run_ids) if run_ids else select_latest_runs(logs, benchmark, sf, instance, storage, engine, table_format)
    if runs.empty:
        return runs

    selected = set(runs["run_id"])
    time = pd.read_csv(time_path)
    df = time[time["run_id"].isin(selected)].copy()
    # time.csv has no table_format / start time — bring them over from logs.csv via run_id.
    df = df.merge(runs[["run_id", "table_format", "benchmark_start_time"]], on="run_id", how="left")

    # Per-query elapsed comes from the wall-clock start/end timestamps.
    df["query_start_time"] = pd.to_datetime(df["query_start_time"])
    df["query_end_time"] = pd.to_datetime(df["query_end_time"])
    df["elapsed_seconds"] = (df["query_end_time"] - df["query_start_time"]).dt.total_seconds()

    # Drop failed queries — they have no meaningful latency.
    df = df[df["error"].isna()]
    df["label"] = (df["engine"] + " " + df["engine_version"].astype(str)
                   + " " + df["table_format"].astype(str)
                   + " sf" + df["scale_factor"].astype(str))
    return df


def plot(df: pd.DataFrame, benchmark: str, output: Path | None) -> None:
    # Order series by engine, then table format, then version — so each engine's bars
    # are contiguous and formats group together (rather than sorting on the raw label,
    # which interleaves formats by version number).
    labels = (df.sort_values(["engine", "table_format", "engine_version", "scale_factor"])
                ["label"].drop_duplicates().tolist())
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
    ax.set_title(f"{benchmark.capitalize()} query latency by engine/version/format — {sfs} on {instances}\n"
                 f"(most recent run per engine/version/format, median, min/max across runs)")
    ax.set_xlabel("Query")
    ax.set_ylabel("Elapsed (s)")
    ax.legend(title="Engine version + format", bbox_to_anchor=(1.01, 1), loc="upper left")
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
    parser.add_argument("--engine", default=None, help="Filter to one engine (e.g. duckdb)")
    parser.add_argument("--table-format", default=None, help="Filter to one table format (e.g. iceberg, ducklake)")
    parser.add_argument("--storage", default=None,
                        help="Filter by storage: 'remote', 'local', or a service (s3/gcs/azure)")
    parser.add_argument("--run-ids", nargs="+", default=None,
                        help="Plot exactly these run_ids (bypasses the latest-per-engine/format selection and other filters)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/<benchmark>.png)")
    args = parser.parse_args()

    df = load(args.results_dir, args.benchmark, args.sf, args.instance, args.storage, args.engine, args.table_format, args.run_ids)
    if df.empty:
        print(f"No {args.benchmark} results found for the given filters.", file=sys.stderr)
        sys.exit(1)

    print("Plotting runs:")
    for label, started in (df[["label", "benchmark_start_time"]].drop_duplicates()
                            .sort_values("label").itertuples(index=False)):
        print(f"  {label}: started {started}")

    output = args.output or (DEFAULT_OUTPUT_DIR / f"{args.benchmark}.png")
    plot(df, args.benchmark, output)


if __name__ == "__main__":
    main()
