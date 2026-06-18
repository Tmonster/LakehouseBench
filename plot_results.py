"""
Plot the analytical benchmark: per-query latency by engine.

Reads the append-only record files (results/logs.csv + results/time.csv), and plots
the most recent run per (engine, engine_version, catalog_service, table_format) for the
selected benchmark — so different engine versions, catalogs (aws-glue / aws-s3tables /
ducklake), and table formats (iceberg / ducklake / delta) each show as separate series.
Bars show the median query time across that run's repetitions, with min/max whiskers.

--sf and --instance are required (one scale factor + instance type per plot), unless
--run-ids is used to hand-pick exact runs. Use --engine (one or more), --catalog,
--table-format, and --storage to further scope the comparison.
--storage takes 'remote', 'local', or a specific service (s3/gcs/azure).

Defaults to the analytical benchmark; other benchmark types have their own per-query
semantics, but --benchmark lets you point this at them.

Usage:
    uv run --extra plot python plot_results.py --sf 10 --instance m5.8xlarge
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge --engine duckdb spark
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge --catalog aws-glue
    uv run --extra plot python plot_results.py --run-ids 1a2b3c 4d5e6f   # exact runs
    uv run --extra plot python plot_results.py --sf 10 --instance m5.8xlarge --output analytical.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

DEFAULT_OUTPUT_DIR = Path("results/images/tmp")


def ensure_catalog_name(logs: pd.DataFrame) -> pd.DataFrame:
    """
    Back-compat: logs written before catalog_name existed have no such column (or null
    values). Fall back to catalog_service so old runs still group and plot.
    """
    if "catalog_name" not in logs.columns:
        logs["catalog_name"] = logs.get("catalog_service")
    logs["catalog_name"] = logs["catalog_name"].fillna(logs.get("catalog_service"))
    return logs


def select_latest_runs(
    logs: pd.DataFrame, benchmark: str, sf: int, instance: str,
    storage: str | None, engines: list[str] | None, table_format: str | None,
    catalog: str | None,
) -> pd.DataFrame:
    """
    One row per (engine, engine_version, catalog_name, table_format): the most recent
    matching run after filters — so different engine versions, catalog configs, and table
    formats each appear as their own series rather than collapsing together. catalog_name
    (not catalog_service) is the series key, so two configs hitting the same remote catalog
    with different table properties plot side by side. Scale factor and instance type are
    required filters (one environment per plot).
    """
    sel = logs[logs["benchmark"] == benchmark].copy()
    sel = sel[sel["scale_factor"] == sf]
    sel = sel[sel["bench_instance_type"] == instance]
    if engines is not None:
        sel = sel[sel["engine"].isin(engines)]
    if table_format is not None:
        sel = sel[sel["table_format"] == table_format]
    if catalog is not None:
        sel = sel[sel["catalog_name"] == catalog]
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
    sel["catalog_name"] = sel["catalog_name"].fillna("unknown")
    sel["engine_version"] = sel["engine_version"].fillna("unknown")
    sel["benchmark_start_time"] = pd.to_datetime(sel["benchmark_start_time"])
    runs = (sel.sort_values("benchmark_start_time")
               .groupby(["engine", "engine_version", "catalog_name", "table_format"],
                        as_index=False).tail(1))

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
    runs["catalog_name"] = runs["catalog_name"].fillna("unknown")
    runs["benchmark_start_time"] = pd.to_datetime(runs["benchmark_start_time"])
    return runs


def load(
    results_dir: Path, benchmark: str, sf: int, instance: str,
    storage: str | None, engines: list[str] | None, table_format: str | None,
    catalog: str | None, run_ids: list[str] | None,
) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    time_path = results_dir / "time.csv"
    for p in (logs_path, time_path):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            sys.exit(1)

    logs = ensure_catalog_name(pd.read_csv(logs_path))
    # Explicit --run-ids take precedence over the latest-per-engine/format selection.
    runs = select_run_ids(logs, run_ids) if run_ids else select_latest_runs(logs, benchmark, sf, instance, storage, engines, table_format, catalog)
    if runs.empty:
        return runs

    selected = set(runs["run_id"])
    time = pd.read_csv(time_path)
    df = time[time["run_id"].isin(selected)].copy()
    # time.csv has no table_format / catalog / start time — bring them over from logs.csv via run_id.
    df = df.merge(runs[["run_id", "table_format", "catalog_name", "benchmark_start_time"]],
                  on="run_id", how="left")

    # Per-query elapsed comes from the wall-clock start/end timestamps.
    df["query_start_time"] = pd.to_datetime(df["query_start_time"])
    df["query_end_time"] = pd.to_datetime(df["query_end_time"])
    df["elapsed_seconds"] = (df["query_end_time"] - df["query_start_time"]).dt.total_seconds()

    # Drop failed queries — they have no meaningful latency.
    df = df[df["error"].isna()]
    df["label"] = (df["engine"] + " " + df["engine_version"].astype(str)
                   + " " + df["catalog_name"].astype(str)
                   + " " + df["table_format"].astype(str)
                   + " sf" + df["scale_factor"].astype(str))
    return df


def plot(df: pd.DataFrame, benchmark: str, output: Path | None) -> None:
    # Order series by engine, then catalog, then table format, then version — so each
    # engine's bars are contiguous and catalogs/formats group together (rather than
    # sorting on the raw label, which interleaves them by version number).
    labels = (df.sort_values(["engine", "catalog_name", "table_format", "engine_version", "scale_factor"])
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
    ax.set_title(f"{benchmark.capitalize()} query latency by engine/version/catalog/format — {sfs} on {instances}\n"
                 f"(most recent run per engine/version/catalog/format, median, min/max across runs)")
    ax.set_xlabel("Query")
    ax.set_ylabel("Elapsed (s)")
    ax.legend(title="Engine version + catalog + format", bbox_to_anchor=(1.01, 1), loc="upper left")
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
    parser.add_argument("--sf", type=int, default=None,
                        help="Scale factor (required unless --run-ids is given)")
    parser.add_argument("--instance", default=None,
                        help="bench_instance_type (required unless --run-ids is given)")
    parser.add_argument("--engine", nargs="+", default=None,
                        help="Filter to these engine(s), e.g. --engine duckdb spark")
    parser.add_argument("--table-format", default=None, help="Filter to one table format (e.g. iceberg, ducklake)")
    parser.add_argument("--catalog", default=None,
                        help="Filter to one catalog_name (the config's catalog_name, "
                             "defaulting to its type, e.g. aws-glue / aws-s3tables / a custom label)")
    parser.add_argument("--storage", default=None,
                        help="Filter by storage: 'remote', 'local', or a service (s3/gcs/azure)")
    parser.add_argument("--run-ids", nargs="+", default=None,
                        help="Plot exactly these run_ids (bypasses the latest-per-engine/catalog/format selection and other filters)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/<benchmark>.png)")
    args = parser.parse_args()

    # Scale factor and instance type pin a single environment per plot; required
    # unless the user is hand-picking exact runs with --run-ids.
    if not args.run_ids:
        missing = [name for name, val in (("--sf", args.sf), ("--instance", args.instance)) if val is None]
        if missing:
            parser.error(f"the following arguments are required: {', '.join(missing)} (or use --run-ids)")

    df = load(args.results_dir, args.benchmark, args.sf, args.instance, args.storage,
              args.engine, args.table_format, args.catalog, args.run_ids)
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
