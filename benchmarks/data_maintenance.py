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
        sql = _function_sql(fn)
        start_iso = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        error = None
        try:
            engine.run_maintenance(sql, namespace)
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


def run_single(runner: BenchmarkRunner, namespace: str, data_dir: Path, u: int) -> list[OpResult]:
    """
    Run and time exactly one maintenance round `u` against the current table state.

    Unlike run(), which always applies rounds 1..N from scratch, this applies only round u
    — the incremental step used to advance a persistent table one round at a time (the
    --dm-round-only / lifecycle pattern). The caller is responsible for having applied
    rounds 1..u-1 already; each round's update set is an independent increment. Rows are
    tagged with round=u, so they slot into the same per-round plots as run().
    """
    if u < 1:
        raise ValueError("--dm-round-only requires a round number >= 1")
    print(f"Running data-maintenance round {u} (single, timed)...")
    return apply_round(runner.engine, namespace, data_dir, u, record=True)
