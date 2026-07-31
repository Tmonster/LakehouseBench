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

--label-format is a str.format template controlling the legend series text, e.g.
"{engine} {version} {catalog}" to drop the table format and scale factor. Available
fields: engine, version/engine_version, catalog/catalog_name, format/table_format,
sf/scale_factor, instance/bench_instance_type, run_id.
--title replaces the generated plot title with text of your own.
--rename overrides a field's value before labelling, e.g.
--rename version:1.6.0.dev284=2.0.0 to show a dev build under its release name.

Usage:
    uv run --extra plot python plot_results.py --sf 10 --instance m5.8xlarge
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge --engine duckdb spark
    uv run --extra plot python plot_results.py --sf 100 --instance m5.8xlarge --catalog aws-glue
    uv run --extra plot python plot_results.py --run-ids 1a2b3c 4d5e6f   # exact runs
    uv run --extra plot python plot_results.py --sf 10 --instance m5.8xlarge --output analytical.png
    uv run --extra plot python plot_results.py --sf 10 --instance m5.8xlarge \
        --label-format "{engine} {version} {catalog}"
"""
from __future__ import annotations

import argparse
import string
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

DEFAULT_OUTPUT_DIR = Path("results/images/tmp")

# Placeholders accepted by --label-format, mapped to the column backing each one. Short
# aliases sit alongside the raw column names so templates stay readable.
LABEL_FIELDS = {
    "engine": "engine",
    "version": "engine_version",
    "engine_version": "engine_version",
    "catalog": "catalog_name",
    "catalog_name": "catalog_name",
    "format": "table_format",
    "table_format": "table_format",
    "sf": "scale_factor",
    "scale_factor": "scale_factor",
    "instance": "bench_instance_type",
    "bench_instance_type": "bench_instance_type",
    "run_id": "run_id",
}

DEFAULT_LABEL_FORMAT = "{engine} {version} {catalog} {format} sf{sf}"


def label_format_fields(fmt: str) -> list[str]:
    """Field names referenced by a --label-format template, in order, deduplicated."""
    parsed = string.Formatter().parse(fmt)   # raises ValueError on malformed braces
    names = [name for _, name, _, _ in parsed if name is not None]
    # Strip any attribute/index access so {sf[0]}-style templates report the base field.
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name.split(".")[0].split("[")[0], None)
    return list(seen)


def validate_label_format(fmt: str) -> None:
    """Raise ValueError with the valid field list if the template can't be rendered."""
    try:
        fields = label_format_fields(fmt)
    except ValueError as e:
        raise ValueError(f"malformed --label-format template: {e}") from None
    if any(f == "" or f.isdigit() for f in fields):
        raise ValueError("--label-format uses named fields only, e.g. '{engine} {version}'")
    unknown = sorted(set(fields) - set(LABEL_FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) in --label-format: {', '.join(unknown)}. "
                         f"Available: {', '.join(sorted(LABEL_FIELDS))}")


def make_labels(df: pd.DataFrame, fmt: str) -> pd.Series:
    """
    Render the template once per row into the series label driving hue and the legend.
    Everything is stringified first, so scale factor 10 reads as 'sf10' rather than
    'sf10.0' — meaning numeric format specs (e.g. {sf:0.1f}) are not supported.
    """
    available = {alias: col for alias, col in LABEL_FIELDS.items() if col in df.columns}
    missing = sorted(set(label_format_fields(fmt)) - set(available))
    if missing:
        raise ValueError(f"--label-format field(s) not present in these results: "
                         f"{', '.join(missing)}")
    values = pd.DataFrame({alias: df[col].astype(str) for alias, col in available.items()},
                          index=df.index)
    return values.apply(lambda row: fmt.format(**row.to_dict()), axis=1)


def parse_renames(specs: list[str]) -> list[tuple[str, str, str]]:
    """
    Parse --rename 'field:old=new' specs into (column, old, new) triples. The field is
    any --label-format field name, so --rename version:1.6.0.dev284=2.0.0 rewrites
    engine_version. Split on the first ':' and the first '=' after it, so values
    containing either character still work as the replacement text.
    """
    parsed = []
    for spec in specs:
        field, sep, rest = spec.partition(":")
        old, eq, new = rest.partition("=")
        if not sep or not eq or not field or not old:
            raise ValueError(f"malformed --rename {spec!r}, expected 'field:old=new' "
                             f"e.g. 'version:1.6.0.dev284=2.0.0'")
        if field not in LABEL_FIELDS:
            raise ValueError(f"unknown field {field!r} in --rename. "
                             f"Available: {', '.join(sorted(LABEL_FIELDS))}")
        parsed.append((LABEL_FIELDS[field], old, new))
    return parsed


def apply_renames(df: pd.DataFrame, renames: list[tuple[str, str, str]]) -> pd.DataFrame:
    """
    Rewrite matching cell values in place, before labels are built — so the rename also
    flows into series ordering and the run list printed at the end. Matching is exact on
    the stringified value; a spec that matches nothing is an error rather than a silent
    no-op, since it usually means a typo'd version string.
    """
    for col, old, new in renames:
        if col not in df.columns:
            raise ValueError(f"--rename field backing column {col!r} not present in these results")
        matches = df[col].astype(str) == old
        if not matches.any():
            present = ", ".join(sorted(df[col].astype(str).unique()))
            raise ValueError(f"--rename found no {col} equal to {old!r}. Present: {present}")
        df.loc[matches, col] = new
    return df


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
    storage: str | None, engines: list[str] | None, engine_versions: list[str] | None,
    table_format: str | None, catalogs: list[str] | None,
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
    if engine_versions is not None:
        # Versions come from the CLI as strings; match against stringified column values.
        sel = sel[sel["engine_version"].astype(str).isin([str(v) for v in engine_versions])]
    if table_format is not None:
        sel = sel[sel["table_format"] == table_format]
    if catalogs is not None:
        sel = sel[sel["catalog_name"].isin(catalogs)]
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
    storage: str | None, engines: list[str] | None, engine_versions: list[str] | None,
    table_format: str | None, catalogs: list[str] | None, run_ids: list[str] | None,
    label_format: str = DEFAULT_LABEL_FORMAT,
    renames: list[tuple[str, str, str]] | None = None,
) -> pd.DataFrame:
    logs_path = results_dir / "logs.csv"
    time_path = results_dir / "time.csv"
    for p in (logs_path, time_path):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            sys.exit(1)

    logs = ensure_catalog_name(pd.read_csv(logs_path))
    # Explicit --run-ids take precedence over the latest-per-engine/format selection.
    runs = select_run_ids(logs, run_ids) if run_ids else select_latest_runs(logs, benchmark, sf, instance, storage, engines, engine_versions, table_format, catalogs)
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
    if renames:
        df = apply_renames(df, renames)
    df["label"] = make_labels(df, label_format)
    return df


def plot(df: pd.DataFrame, benchmark: str, output: Path | None,
         label_format: str = DEFAULT_LABEL_FORMAT, title: str | None = None) -> None:
    # Order series by engine, then catalog, then table format, then version — so each
    # engine's bars are contiguous and catalogs/formats group together (rather than
    # sorting on the raw label, which interleaves them by version number).
    labels = (df.sort_values(["engine", "catalog_name", "table_format", "engine_version", "scale_factor"])
                ["label"].drop_duplicates().tolist())
    queries = sorted(df["query"].unique())

    sns.set_theme(style="whitegrid", font_scale=0.9)
    palette = sns.color_palette("tab10", n_colors=len(labels))

    # Widen with the number of query groups so bars aren't squeezed (TPC-H has ~22).
    fig_width = max(14, len(queries) * 0.7)
    fig, ax = plt.subplots(figsize=(fig_width, 6))
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
    # An explicit --title replaces the generated one outright; \n in it still breaks lines.
    ax.set_title(title if title is not None else
                 f"{benchmark.capitalize()} query latency by engine/version/catalog/format — {sfs} on {instances}\n"
                 f"(most recent run per engine/version/catalog/format, median, min/max across runs)")
    ax.set_xlabel("Query")
    ax.set_ylabel("Elapsed (s)")
    # Name the legend after whatever the labels actually contain, so a custom
    # --label-format doesn't leave a stale heading above the series.
    legend_title = ("Engine version + catalog + format" if label_format == DEFAULT_LABEL_FORMAT
                    else " + ".join(label_format_fields(label_format)))
    # Legend beneath the plot, spread across columns, so the axes use the full width.
    ax.legend(title=legend_title,
              loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=min(len(labels), 4))
    fig.tight_layout()

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        # bbox_inches="tight" keeps the below-axes legend from being clipped.
        fig.savefig(output, dpi=150, bbox_inches="tight")
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
    parser.add_argument("--engine-version", nargs="+", default=None,
                        help="Filter to these engine version(s), e.g. --engine-version 1.5.2 1.5.3")
    parser.add_argument("--table-format", default=None, help="Filter to one table format (e.g. iceberg, ducklake)")
    parser.add_argument("--catalog", nargs="+", default=None,
                        help="Filter to these catalog_name(s) — the config's catalog_name, "
                             "defaulting to its type. e.g. --catalog aws-glue glue_target_file_size_bytes_500mb")
    parser.add_argument("--storage", default=None,
                        help="Filter by storage: 'remote', 'local', or a service (s3/gcs/azure)")
    parser.add_argument("--run-ids", nargs="+", default=None,
                        help="Plot exactly these run_ids (bypasses the latest-per-engine/catalog/format selection and other filters)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save to file (default: results/images/tmp/<benchmark>.png)")
    parser.add_argument("--title", default=None,
                        help="Replace the generated plot title with this text "
                             "(use \\n for a second line)")
    parser.add_argument("--label-format", default=DEFAULT_LABEL_FORMAT,
                        help="str.format template for the legend series label "
                             f"(default: {DEFAULT_LABEL_FORMAT!r}). "
                             f"Fields: {', '.join(sorted(LABEL_FIELDS))}")
    parser.add_argument("--rename", nargs="+", default=None, metavar="FIELD:OLD=NEW",
                        help="Override a field value before labelling, e.g. "
                             "--rename version:1.6.0.dev284=2.0.0. Repeatable; also "
                             "affects series ordering and the printed run list")
    args = parser.parse_args()

    try:
        validate_label_format(args.label_format)
        renames = parse_renames(args.rename or [])
    except ValueError as e:
        parser.error(str(e))

    # The shell passes "\n" through as two characters; turn it into a real line break so
    # a two-line custom title works the way the generated one does.
    if args.title is not None:
        args.title = args.title.replace("\\n", "\n")

    # Scale factor and instance type pin a single environment per plot; required
    # unless the user is hand-picking exact runs with --run-ids.
    if not args.run_ids:
        missing = [name for name, val in (("--sf", args.sf), ("--instance", args.instance)) if val is None]
        if missing:
            parser.error(f"the following arguments are required: {', '.join(missing)} (or use --run-ids)")

    try:
        df = load(args.results_dir, args.benchmark, args.sf, args.instance, args.storage,
                  args.engine, args.engine_version, args.table_format, args.catalog,
                  args.run_ids, args.label_format, renames)
    except ValueError as e:
        parser.error(str(e))
    if df.empty:
        print(f"No {args.benchmark} results found for the given filters.", file=sys.stderr)
        sys.exit(1)

    print("Plotting runs:")
    runs_info = (df[["label", "run_id", "benchmark_start_time"]]
                 .drop_duplicates().sort_values("label"))
    for label, run_id, started in runs_info.itertuples(index=False):
        print(f"  {run_id}  {label}  (started {started})")
    # A --label-format that omits a distinguishing field makes separate runs share a
    # label, which silently pools them into one bar. Say so rather than plotting a lie.
    collapsed = runs_info["label"].value_counts()
    for label, count in collapsed[collapsed > 1].items():
        print(f"warning: {count} runs share the label {label!r} and will be pooled into "
              f"one series — add a field to --label-format to separate them",
              file=sys.stderr)
    # Copy-pasteable: pin this exact set of runs regardless of future data.
    print(f"\nReplot exactly these runs:\n  --run-ids {' '.join(runs_info['run_id'].tolist())}")

    output = args.output or (DEFAULT_OUTPUT_DIR / f"{args.benchmark}.png")
    plot(df, args.benchmark, output, args.label_format, args.title)


if __name__ == "__main__":
    main()
