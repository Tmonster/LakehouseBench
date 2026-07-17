"""
Record benchmark results to two append-only CSV files.

logs.csv  — one row per benchmark invocation (run-level facts + the headline scores).
time.csv  — one row per query execution (per-query timing + correctness + errors).

The two join on run_id; time.csv repeats some run-level columns on purpose so it
reads sensibly on its own when inspected by hand.

CSV is written via DuckDB's CSV writer so quoting/escaping of free-text fields
(notably `error`) is handled correctly. Files are genuinely append-only: the header
is written once when the file is first created, and every subsequent run appends
its rows to the end without rewriting what is already there.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from engines.duckdb.connect import connect as duckdb_connect

from benchmarks.runner import QueryResult

LOGS_FILENAME = "logs.csv"
TIME_FILENAME = "time.csv"

# (column_name, duckdb_type) — tuple order below must match these exactly.
LOGS_COLUMNS = [
    ("run_id", "VARCHAR"),
    ("benchmark_start_time", "VARCHAR"),
    ("benchmark_end_time", "VARCHAR"),
    ("bench_instance_type", "VARCHAR"),
    ("benchmark", "VARCHAR"),
    ("namespace", "VARCHAR"),
    ("scale_factor", "BIGINT"),
    ("engine", "VARCHAR"),
    ("engine_version", "VARCHAR"),
    # Benchmark suite the run belongs to ("tpch" or "tpcds"). Distinguishes otherwise
    # identical analytical/load rows across the two suites.
    ("suite", "VARCHAR"),
    ("table_format", "VARCHAR"),
    ("catalog_service", "VARCHAR"),
    ("catalog_name", "VARCHAR"),
    ("catalog_region", "VARCHAR"),
    ("storage_service", "VARCHAR"),
    ("storage_region", "VARCHAR"),
    ("power_score", "DOUBLE"),
    ("throughput_score", "DOUBLE"),
    ("composite_score", "DOUBLE"),
    # TPC-DS data-maintenance / compaction context. dm_rounds is the number of
    # maintenance rounds applied for the run; the file counts are the catalog state
    # before/after a compaction run (NULL for non-compaction runs).
    ("dm_rounds", "BIGINT"),
    ("files_before", "BIGINT"),
    ("files_after", "BIGINT"),
    ("delete_files_before", "BIGINT"),
    ("delete_files_after", "BIGINT"),
]

TIME_COLUMNS = [
    ("run_id", "VARCHAR"),
    ("engine", "VARCHAR"),
    ("engine_version", "VARCHAR"),
    # Benchmark suite ("tpch"/"tpcds"); see LOGS_COLUMNS.
    ("suite", "VARCHAR"),
    ("scale_factor", "BIGINT"),
    ("bench_instance_type", "VARCHAR"),
    ("benchmark", "VARCHAR"),
    ("namespace", "VARCHAR"),
    ("query", "VARCHAR"),
    ("run", "BIGINT"),
    ("query_start_time", "VARCHAR"),
    ("query_end_time", "VARCHAR"),
    ("result_correct", "BOOLEAN"),
    ("error", "VARCHAR"),
    ("rows_returned", "BIGINT"),
    # dm_rounds tags every row of a maintenance/compaction/after-maintenance run; the
    # file counts are populated only on compaction rows (NULL elsewhere).
    ("dm_rounds", "BIGINT"),
    ("files_before", "BIGINT"),
    ("files_after", "BIGINT"),
    ("delete_files_before", "BIGINT"),
    ("delete_files_after", "BIGINT"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bench_instance_type() -> str:
    # run_benchmark.py guarantees this is set before any benchmark runs.
    return os.environ.get("BENCH_INSTANCE_TYPE", "")


# ---------------------------------------------------------------------------
# Append (DuckDB CSV writer; header once, then byte-append)
# ---------------------------------------------------------------------------

def _append_rows(target: Path, columns: list[tuple[str, str]], rows: list[tuple]) -> None:
    if not rows:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    cols_ddl = ", ".join(f'"{name}" {dtype}' for name, dtype in columns)
    placeholders = ", ".join(["?"] * len(columns))

    with duckdb_connect() as conn:
        conn.execute(f"CREATE TABLE _staging ({cols_ddl})")
        conn.executemany(f"INSERT INTO _staging VALUES ({placeholders})", rows)

        if target.exists():
            # Append: write the new rows (no header) to a temp file, then concat bytes.
            part = target.parent / (target.name + ".part")
            conn.execute(f"COPY _staging TO '{part}' (FORMAT CSV, HEADER FALSE)")
            with open(target, "ab") as out, open(part, "rb") as new:
                shutil.copyfileobj(new, out)
            part.unlink()
        else:
            conn.execute(f"COPY _staging TO '{target}' (FORMAT CSV, HEADER TRUE)")


def append_log(result_dir: Path, row: tuple) -> Path:
    target = result_dir / LOGS_FILENAME
    _append_rows(target, LOGS_COLUMNS, [row])
    return target


def append_times(result_dir: Path, rows: list[tuple]) -> Path:
    target = result_dir / TIME_FILENAME
    _append_rows(target, TIME_COLUMNS, rows)
    return target


# ---------------------------------------------------------------------------
# Row builders (return tuples in the column order declared above)
# ---------------------------------------------------------------------------

def log_row(
    *,
    run_id: str,
    benchmark_start_time: str,
    benchmark_end_time: str,
    bench_instance_type: str,
    benchmark: str,
    namespace: str,
    scale_factor: int,
    engine: str,
    engine_version: str,
    table_format: str | None = None,
    catalog_service: str | None = None,
    catalog_name: str | None = None,
    catalog_region: str | None = None,
    storage_service: str | None = None,
    storage_region: str | None = None,
    power_score: float | None = None,
    throughput_score: float | None = None,
    composite_score: float | None = None,
    dm_rounds: int | None = None,
    files_before: int | None = None,
    files_after: int | None = None,
    delete_files_before: int | None = None,
    delete_files_after: int | None = None,
    suite: str | None = None,
) -> tuple:
    return (
        run_id, benchmark_start_time, benchmark_end_time, bench_instance_type,
        benchmark, namespace, scale_factor, engine, engine_version, suite,
        table_format, catalog_service, catalog_name, catalog_region, storage_service, storage_region,
        power_score, throughput_score, composite_score,
        dm_rounds, files_before, files_after, delete_files_before, delete_files_after,
    )


def time_row_from_query(
    qr: QueryResult, *, run_id: str, bench_instance_type: str, engine_version: str,
    dm_rounds: int | None = None, suite: str | None = None,
) -> tuple:
    return (
        run_id, qr.engine, engine_version, suite, qr.scale_factor, bench_instance_type,
        qr.benchmark, qr.namespace, qr.query, qr.run,
        qr.query_start_time, qr.query_end_time, qr.result_correct, qr.error, qr.rows_returned,
        dm_rounds, None, None, None, None,
    )


def time_row_from_refresh(
    rf, *, run_id: str, bench_instance_type: str, engine: str, engine_version: str,
    scale_factor: int, benchmark: str, namespace: str, dm_rounds: int | None = None,
    suite: str | None = None,
) -> tuple:
    # rf.rf is RF1/RF2/RF; encode the update set so concurrent refresh rows stay distinct.
    query = f"{rf.rf}_set{rf.set_n}"
    return (
        run_id, engine, engine_version, suite, scale_factor, bench_instance_type,
        benchmark, namespace, query, 0,
        rf.query_start_time, rf.query_end_time, None, rf.error, None,
        dm_rounds, None, None, None, None,
    )


def time_row_from_dm_op(
    op, *, run_id: str, bench_instance_type: str, engine: str, engine_version: str,
    scale_factor: int, benchmark: str, namespace: str, dm_rounds: int | None = None,
    suite: str | None = None,
) -> tuple:
    # op is a data_maintenance.OpResult; encode the round so per-round ops stay distinct.
    query = f"{op.function}_round{op.round}"
    return (
        run_id, engine, engine_version, suite, scale_factor, bench_instance_type,
        benchmark, namespace, query, 0,
        op.query_start_time, op.query_end_time, None, op.error, None,
        dm_rounds, None, None, None, None,
    )


def time_row_from_compaction(
    result, *, run_id: str, bench_instance_type: str, engine: str, engine_version: str,
    scale_factor: int, namespace: str, dm_rounds: int | None = None,
    suite: str | None = None,
) -> tuple:
    # The compaction row carries the before/after catalog file counts in the dedicated columns.
    return (
        run_id, engine, engine_version, suite, scale_factor, bench_instance_type,
        "compaction", namespace, "compaction", 0,
        result.query_start_time, result.query_end_time, None, result.error, None,
        dm_rounds,
        result.stats_before["file_count"], result.stats_after["file_count"],
        result.stats_before["delete_file_count"], result.stats_after["delete_file_count"],
    )


def time_row_for_load(
    *, run_id: str, bench_instance_type: str, engine: str, engine_version: str,
    scale_factor: int, namespace: str, query_start_time: str, query_end_time: str,
    error: str | None, dm_rounds: int | None = None, suite: str | None = None,
) -> tuple:
    return (
        run_id, engine, engine_version, suite, scale_factor, bench_instance_type,
        "load", namespace, "load", 0,
        query_start_time, query_end_time, None, error, None,
        dm_rounds, None, None, None, None,
    )
