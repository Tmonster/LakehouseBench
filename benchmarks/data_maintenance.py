"""
TPC-DS Data Maintenance (DM).

Applies spec-faithful maintenance rounds to the loaded warehouse. Each round stages
one update set's source Parquet (dm/round_<u>/) and runs the maintenance functions in
order — Load-Fact inserts before Delete-Fact deletes — committing one function at a
time so every function produces its own snapshot/small-file batch (the churn that
degenerates the table for the compaction benchmark).

Two entry points:
  * run()              — times each function and returns per-op results (the `maintenance`
                         benchmark).
  * provision_rounds() — applies rounds untimed, to bring a table into a known degenerate
                         state before an analytical or compaction benchmark.

Scope: all three sales channels (store/catalog/web) sales+returns are implemented — the
LF_* load-fact inserts and DF_* delete-fact deletes. Inventory and the dimension SCD
updates follow the same staging → LF → DF structure and are added as further entries in
DM_FUNCTIONS / the maintenance SQL directory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.runner import BenchmarkRunner

MAINTENANCE_DIR = Path("queries/tpcds/maintenance")

# Ordered list of maintenance functions applied per round. Load-Fact (lf_*) inserts
# run before Delete-Fact (df_*) deletes, per the TPC-DS data-maintenance model.
DM_FUNCTIONS: list[str] = [
    "lf_ss", "lf_sr", "lf_cs", "lf_cr", "lf_ws", "lf_wr",
    "df_ss", "df_cs", "df_ws",
]

# Delete-Fact channels: each df_* function deletes from a sales fact and its correlated
# returns fact, keyed on the sales/returned date surrogate key. Rather than one DELETE
# that joins date_dim against the whole dm_delete table, each fact is deleted with a
# parameterized statement (df.sql) run once per date range in the round's delete.parquet —
# a plan that scales far better on Spark and on DuckDB at high SF. The union of the ranges
# is identical to the old form, and matches the spec's per-range application (Clause 5.3.8).
DELETE_FACTS: dict[str, list[tuple[str, str]]] = {
    "df_ss": [("store_sales", "ss_sold_date_sk"), ("store_returns", "sr_returned_date_sk")],
    "df_cs": [("catalog_sales", "cs_sold_date_sk"), ("catalog_returns", "cr_returned_date_sk")],
    "df_ws": [("web_sales", "ws_sold_date_sk"), ("web_returns", "wr_returned_date_sk")],
}


def _delete_fact_statements(fn: str) -> list[str]:
    """Parameterized DELETE statements (one per fact table) for a df_* function.

    Line comments and the trailing ';' are stripped so each item is a single clean
    statement — Spark's sql() rejects a trailing semicolon.
    """
    template = _function_sql("df")
    body = "\n".join(line.split("--", 1)[0] for line in template.splitlines())
    body = body.strip().rstrip(";").strip()
    return [body.format(table=t, date_sk=c) for t, c in DELETE_FACTS[fn]]


@dataclass
class OpResult:
    function: str            # e.g. "lf_ss"
    round: int
    elapsed_seconds: float
    query_start_time: str
    query_end_time: str
    error: str | None = None


def round_dir(data_dir: Path, u: int) -> Path:
    return data_dir / "dm" / f"round_{u}"


def has_dm_data(data_dir: Path, rounds: int) -> bool:
    return all(round_dir(data_dir, u).is_dir() for u in range(1, rounds + 1))


def _function_sql(name: str) -> str:
    return (MAINTENANCE_DIR / f"{name}.sql").read_text()


def apply_round(engine, namespace: str, data_dir: Path, u: int, record: bool = True) -> list[OpResult]:
    """
    Apply one maintenance round. Stages the round's source Parquet, then runs each
    DM function. When record is True, each function is timed into an OpResult.
    """
    rd = round_dir(data_dir, u)
    if not rd.is_dir():
        raise FileNotFoundError(
            f"Missing DM round {u} at {rd}. Generate it with "
            f"`python -m setup.generate_data --suite tpcds --sf <N> --dm-sets {u}`."
        )
    engine.load_staging(rd, namespace)

    results: list[OpResult] = []
    for fn in DM_FUNCTIONS:
        start_iso = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        error = None
        try:
            if fn in DELETE_FACTS:
                # Delete-Fact: run each fact's parameterized DELETE once per date range
                # in the staged dm_delete table (see df.sql / DELETE_FACTS).
                engine.run_delete_fact(_delete_fact_statements(fn), namespace)
            else:
                engine.run_maintenance(_function_sql(fn), namespace)
        except Exception as e:
            error = str(e)
        elapsed = round(time.perf_counter() - start, 4)
        end_iso = datetime.now(timezone.utc).isoformat()
        if record:
            results.append(OpResult(fn, u, elapsed, start_iso, end_iso, error))
        status = f"ERROR: {error}" if error else f"{elapsed:.3f}s"
        print(f"  round {u} {fn}: {status}")
    return results


def provision_rounds(engine, namespace: str, data_dir: Path, rounds: int) -> None:
    """Apply `rounds` maintenance rounds untimed, to reach a degenerate state."""
    print(f"Applying {rounds} data-maintenance round(s) to '{namespace}' (untimed setup)...")
    for u in range(1, rounds + 1):
        apply_round(engine, namespace, data_dir, u, record=False)


def run(runner: BenchmarkRunner, namespace: str, data_dir: Path, rounds: int) -> list[OpResult]:
    """Run and time `rounds` maintenance rounds. Returns per-function timing results."""
    if rounds < 1:
        raise ValueError("maintenance benchmark requires --dm-rounds >= 1")
    print(f"Running {rounds} data-maintenance round(s)...")
    results: list[OpResult] = []
    for u in range(1, rounds + 1):
        results.extend(apply_round(runner.engine, namespace, data_dir, u, record=True))
    return results
