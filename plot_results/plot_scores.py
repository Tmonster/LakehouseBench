"""
Plot TPC-H scores (power / throughput / composite) by engine, version, and table format.

Scores are recorded per run in results/logs.csv. For each score metric this selects the
most recent run per (engine, engine_version, table_format) that *has* that metric:
  power_score      — from power or composite runs
  throughput_score — from throughput or composite runs
  composite_score  — from composite runs
So a single engine/format may source different metrics from different runs.

Bars are grouped by score type; one bar per engine/version/format series. Higher is
better (QphH). Series are ordered by engine, then table format, then version.

Usage:
    uv run --extra plot python plot_scores.py --sf 10
    uv run --extra plot python plot_scores.py --sf 10 --engine duckdb
    uv run --extra plot python plot_scores.py --metric power
    uv run --extra plot python plot_scores.py --run-ids 1a2b3c 4d5e6f
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

DEFAULT_OUTPUT_DIR = Path("results/images/tmp")

# metric name (x-axis / --metric value) -> logs.csv column
METRICS = {
    "power": "power_score",
    "throughput": "throughput_score",
    "composite": "composite_score",
}


def _apply_filters(logs, sf, instance, engine, table_format, storage):
    sel = logs.copy()
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
    return sel


def load_scores(
    results_dir: Path, metrics: list[str], sf, instance, engine, table_format, storage,
    run_ids: list[str] | None,
) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    if not logs_path.exists():
        print(f"error: {logs_path} not found", file=sys.stderr)
        sys.exit(1)

    logs = pd.read_csv(logs_path)
    if run_ids:
        missing = [r for r in run_ids if r not in set(logs["run_id"])]
        if missing:
            print(f"warning: run_id(s) not found in logs.csv: {', '.join(missing)}", file=sys.stderr)
        logs = logs[logs["run_id"].isin(run_ids)]
    else:
        logs = _apply_filters(logs, sf, instance, engine, table_format, storage)
    if logs.empty:
        return pd.DataFrame()

    logs = logs.copy()
    logs["table_format"] = logs["table_format"].fillna("unknown")
    logs["engine_version"] = logs["engine_version"].fillna("unknown")
    logs["benchmark_start_time"] = pd.to_datetime(logs["benchmark_start_time"])
    logs["label"] = (logs["engine"] + " " + logs["engine_version"].astype(str)
                     + " " + logs["table_format"].astype(str)
                     + " sf" + logs["scale_factor"].astype(str))

    keep = ["engine", "engine_version", "table_format", "scale_factor", "label",
            "bench_instance_type", "run_id", "benchmark_start_time"]
    rows: list[dict] = []
    for name in metrics:
        col = METRICS[name]
        have = logs[logs[col].notna()]
        if have.empty:
            continue
        # latest run per series that has this metric
        latest = have.sort_values("benchmark_start_time").groupby("label", as_index=False).tail(1)
        for r in latest.itertuples(index=False):
            row = {k: getattr(r, k) for k in keep}
            row["metric"] = name
            row["score"] = getattr(r, col)
            rows.append(row)
    return pd.DataFrame(rows)


def plot(df: pd.DataFrame, output: Path | None) -> None:
    metric_order = [m for m in ("power", "throughput", "composite") if m in set(df["metric"])]
    labels = (df.sort_values(["engine", "table_format", "engine_version", "scale_factor"])
                ["label"].drop_duplicates().tolist())

    sns.set_theme(style="whitegrid", font_scale=0.9)
    palette = sns.color_palette("tab10", n_colors=len(labels))

    fig, ax = plt.subplots(figsize=(max(6, 2 * len(metric_order) + 2), 5))
    sns.barplot(
        data=df,
        x="metric",
        y="score",
        hue="label",
        order=metric_order,
        hue_order=labels,
        palette=palette,
        ax=ax,
    )

    sfs = "/".join(f"sf{s}" for s in sorted(df["scale_factor"].unique()))
    instances = ", ".join(sorted(df["bench_instance_type"].dropna().unique())) or "unknown"
    ax.set_title(f"TPC-H scores — {sfs} on {instances}\n"
                 f"(most recent run per engine/version/format with each score; higher is better)")
    ax.set_xlabel("Score")
    ax.set_ylabel("QphH")
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
                        help="Directory holding logs.csv (default: results)")
    parser.add_argument("--metric", choices=list(METRICS), default=None,
                        help="Plot only one score (default: all three)")
    parser.add_argument("--sf", type=int, default=None, help="Filter to one scale factor")
    parser.add_argument("--instance", default=None, help="Filter to one bench_instance_type")
    parser.add_argument("--engine", default=None, help="Filter to one engine (e.g. duckdb)")
    parser.add_argument("--table-format", default=None, help="Filter to one table format (e.g. iceberg, ducklake)")
    parser.add_argument("--storage", default=None,
                        help="Filter by storage: 'remote', 'local', or a service (s3/gcs/azure)")
    parser.add_argument("--run-ids", nargs="+", default=None,
                        help="Use exactly these run_ids (bypasses the latest-per selection and other filters)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/scores.png)")
    args = parser.parse_args()

    metrics = [args.metric] if args.metric else list(METRICS)
    df = load_scores(args.results_dir, metrics, args.sf, args.instance, args.engine,
                     args.table_format, args.storage, args.run_ids)
    if df.empty:
        print("No scores found for the given filters.", file=sys.stderr)
        sys.exit(1)

    print("Scores from runs:")
    for r in df.sort_values(["metric", "label"]).itertuples(index=False):
        print(f"  {r.metric:11s} {r.label}: {r.score}  ({r.run_id}, started {r.benchmark_start_time})")

    output = args.output or (DEFAULT_OUTPUT_DIR / "scores.png")
    plot(df, output)


if __name__ == "__main__":
    main()
