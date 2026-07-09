"""
Plot the TPC-DS data-maintenance benchmark: per-round load-fact vs delete-fact time.

The maintenance benchmark records one row per DM function per round (lf_ss_round1,
lf_cs_round1, df_ss_round1, ...). This script collapses the six load-fact functions of a
round into a single `lf_round<n>` bar and the three delete-fact functions into a single
`df_round<n>` bar — each bar is the total time for that group in that round. Series (the
bar colors) are the same engine/version/catalog/format labels as plot_results.py, and it
accepts the same selection options (--sf, --instance, --engine, --engine-version,
--table-format, --catalog, --storage, --run-ids, --output).

Usage:
    uv run --extra plot python plot_maintenance.py --sf 10 --instance m5.8xlarge
    uv run --extra plot python plot_maintenance.py --run-ids 1a2b3c
    uv run --extra plot python plot_maintenance.py --sf 10 --instance c8gd.4xlarge \
        --catalog ducklake-compaction -o results/images/maintenance.png
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Reuse plot_results' run selection + row loading so the option surface stays identical.
from plot_results import load

DEFAULT_OUTPUT_DIR = Path("results/images/tmp")
BENCHMARK = "maintenance"

# lf_ss_round1 / df_cs_round12 -> ("lf"|"df", round number). Anchored so only the DM
# function rows match (analytical query names like "q01" are ignored).
_ROUND_RE = re.compile(r"^(lf|df)_[a-z]+_round(\d+)$")

# Load-fact runs before delete-fact within a round — keep that reading order on the x-axis.
_OP_ORDER = {"lf": 0, "df": 1}


def aggregate_rounds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse per-function rows into per-round group bars: lf_round<n> (sum of the round's
    load-fact functions) and df_round<n> (sum of its delete-fact functions).

    Two-level aggregation: sum the constituent functions *within a run* here (a bar is the
    total time to run that group in that round), then the barplot takes the median across
    runs that share a label (with min/max whiskers), matching plot_results.py.
    """
    parsed = df["query"].str.extract(_ROUND_RE)
    df = df.assign(op=parsed[0], round=pd.to_numeric(parsed[1], errors="coerce"))
    df = df[df["op"].notna()].copy()
    if df.empty:
        return df
    df["round"] = df["round"].astype(int)
    df["group"] = df["op"] + "_round" + df["round"].astype(str)

    keys = ["run_id", "label", "engine", "engine_version", "catalog_name",
            "table_format", "scale_factor", "bench_instance_type", "op", "round", "group"]
    return df.groupby(keys, as_index=False)["elapsed_seconds"].sum()


def plot(df: pd.DataFrame, output: Path | None) -> None:
    # x-axis order: by round then op (lf before df) so each round's two bars sit together.
    order = (df[["group", "round", "op"]].drop_duplicates()
             .assign(_op=lambda d: d["op"].map(_OP_ORDER))
             .sort_values(["round", "_op"])["group"].tolist())
    # Series order matches plot_results: engine, then catalog, format, version, scale.
    labels = (df.sort_values(["engine", "catalog_name", "table_format", "engine_version", "scale_factor"])
                ["label"].drop_duplicates().tolist())

    sns.set_theme(style="whitegrid", font_scale=0.9)
    palette = sns.color_palette("tab10", n_colors=len(labels))
    fig_width = max(12, len(order) * 0.7)
    fig, ax = plt.subplots(figsize=(fig_width, 6))
    sns.barplot(
        data=df,
        x="group",
        y="elapsed_seconds",
        hue="label",
        hue_order=labels,
        order=order,
        estimator="median",
        errorbar=("pi", 100),   # min/max whiskers across runs sharing a label
        palette=palette,
        width=0.9,
        ax=ax,
    )

    sfs = "/".join(f"sf{s}" for s in sorted(df["scale_factor"].unique()))
    instances = ", ".join(sorted(df["bench_instance_type"].dropna().unique())) or "unknown"
    ax.set_title(
        f"Data-maintenance time by round: load-fact vs delete-fact — {sfs} on {instances}\n"
        f"(each bar = total time for that group's functions in the round; median, min/max across runs)"
    )
    ax.set_xlabel("Maintenance group (load-fact / delete-fact per round)")
    ax.set_ylabel("Elapsed (s)")
    ax.legend(title="Engine version + catalog + format",
              loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=min(len(labels), 4))
    fig.tight_layout()

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150, bbox_inches="tight")
        print(f"Saved to {output}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="Directory holding logs.csv and time.csv (default: results)")
    parser.add_argument("--sf", type=int, default=None,
                        help="Scale factor (required unless --run-ids is given)")
    parser.add_argument("--instance", default=None,
                        help="bench_instance_type (required unless --run-ids is given)")
    parser.add_argument("--engine", nargs="+", default=None,
                        help="Filter to these engine(s), e.g. --engine duckdb spark")
    parser.add_argument("--engine-version", nargs="+", default=None,
                        help="Filter to these engine version(s), e.g. --engine-version 1.5.2 1.5.3")
    parser.add_argument("--table-format", default=None, help="Filter to one table format (e.g. iceberg, ducklake)")
    parser.add_argument("--catalog", nargs="+", default=None,
                        help="Filter to these catalog_name(s) — the config's catalog_name, "
                             "defaulting to its type. e.g. --catalog ducklake-compaction")
    parser.add_argument("--storage", default=None,
                        help="Filter by storage: 'remote', 'local', or a service (s3/gcs/azure)")
    parser.add_argument("--run-ids", nargs="+", default=None,
                        help="Plot exactly these run_ids (bypasses the latest-per-engine/catalog/format selection and other filters)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/maintenance.png)")
    args = parser.parse_args()

    if not args.run_ids:
        missing = [name for name, val in (("--sf", args.sf), ("--instance", args.instance)) if val is None]
        if missing:
            parser.error(f"the following arguments are required: {', '.join(missing)} (or use --run-ids)")

    df = load(args.results_dir, BENCHMARK, args.sf, args.instance, args.storage,
              args.engine, args.engine_version, args.table_format, args.catalog, args.run_ids)
    if df.empty:
        print("No maintenance results found for the given filters.", file=sys.stderr)
        sys.exit(1)

    agg = aggregate_rounds(df)
    if agg.empty:
        print("No load-fact/delete-fact round rows found (expected query names like lf_ss_round1).",
              file=sys.stderr)
        sys.exit(1)

    print("Plotting runs:")
    runs_info = (agg[["label", "run_id", "bench_instance_type"]]
                 .drop_duplicates().sort_values("label"))
    for label, run_id, inst in runs_info.itertuples(index=False):
        print(f"  {run_id}  {label}  ({inst})")
    print(f"\nReplot exactly these runs:\n  --run-ids {' '.join(runs_info['run_id'].tolist())}")

    output = args.output or (DEFAULT_OUTPUT_DIR / "maintenance.png")
    plot(agg, output)


if __name__ == "__main__":
    main()
