"""
Plot the compaction benchmark: compaction time vs data-maintenance rounds.

The headline panel shows compaction wall-clock time as a function of how many TPC-DS
data-maintenance rounds were applied before compaction (the --dm-rounds knob). A second
panel shows the pre-compaction data-file count (files_before) for the same runs, because
dm_rounds is only the knob — the file count is the table's actual degeneration, and it
does not grow uniformly across engines/formats/scale factors.

One line per (engine, catalog_name, table_format, scale_factor) series, so DuckLake and
(once implemented) Spark/Iceberg compaction compare directly. Repeated runs at the same
dm_rounds are averaged.

Usage:
    uv run --extra plot python plot_compaction.py
    uv run --extra plot python plot_compaction.py --instance m5.8xlarge --output results/images/compaction.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

LOGS = Path("results/logs.csv")
TIME = Path("results/time.csv")
DEFAULT_OUTPUT = Path("results/images/compaction.png")


def load() -> pd.DataFrame:
    time = pd.read_csv(TIME)
    logs = pd.read_csv(LOGS)
    comp = time[time["benchmark"] == "compaction"].copy()
    if comp.empty:
        sys.exit("No compaction runs found in results/time.csv. Run `--benchmark compaction` first.")
    comp["elapsed"] = (
        pd.to_datetime(comp["query_end_time"]) - pd.to_datetime(comp["query_start_time"])
    ).dt.total_seconds()
    # Bring catalog_name / table_format over from logs for series labels.
    labels = logs[["run_id", "catalog_name", "table_format"]].drop_duplicates("run_id")
    comp = comp.merge(labels, on="run_id", how="left")
    comp["catalog_name"] = comp["catalog_name"].fillna(comp.get("engine"))
    comp["series"] = (
        comp["engine"] + " / " + comp["catalog_name"].astype(str)
        + " / sf" + comp["scale_factor"].astype(str)
    )
    return comp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=None, help="Filter to one bench_instance_type")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    comp = load()
    if args.instance:
        comp = comp[comp["bench_instance_type"] == args.instance]
        if comp.empty:
            sys.exit(f"No compaction runs for instance {args.instance!r}.")

    # Average repeated runs at the same (series, dm_rounds).
    agg = (comp.groupby(["series", "dm_rounds"], dropna=False)
           .agg(elapsed=("elapsed", "mean"), files_before=("files_before", "mean"))
           .reset_index()
           .sort_values("dm_rounds"))

    fig, (ax_t, ax_f) = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    for name, g in agg.groupby("series"):
        ax_t.plot(g["dm_rounds"], g["elapsed"], marker="o", label=name)
        ax_f.plot(g["dm_rounds"], g["files_before"], marker="s", label=name)

    ax_t.set_ylabel("compaction time (s)")
    ax_t.set_title("Compaction time vs data-maintenance rounds")
    ax_t.grid(True, alpha=0.3)
    ax_t.legend(fontsize=8)

    ax_f.set_ylabel("data files before compaction")
    ax_f.set_xlabel("data-maintenance rounds")
    ax_f.set_title("Table degeneration (actual file count)")
    ax_f.grid(True, alpha=0.3)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=120)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
