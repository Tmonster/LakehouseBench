"""
Generate TPC-DS base tables, query files, and answer files using DuckDB's `tpcds`
extension (which embeds dsdgen and the 99 official query templates).

Base data: `CALL dsdgen(sf=N)` populates the 24 TPC-DS tables in an in-memory
DuckDB, which are then written to data/tpcds/sf=N/<table>.parquet.

Queries: the 99 query texts (already parameter-substituted) are written to
queries/tpcds/queries/qNN.sql.

Answers: each query is run against the just-written base tables and its result is
written to queries/tpcds/answers/sfN/qNN.csv — the reference used by the analytical
benchmark to verify correctness at the pristine (round-0) state. Answers are derived
from the same data the benchmark queries, so the two always stay in sync.

Spec-faithful data-maintenance (refresh) sets are NOT produced here — the DuckDB
extension cannot generate them. Those come from the official dsdgen toolkit; see
setup.generate_dm.

Usage:
    python -m setup.generate_tpcds --sf 1
    python -m setup.generate_tpcds --sf 10 --no-answers
    python -m setup.generate_tpcds --sf 1 --data-dir /tmp/tpcds
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from benchmarks.suite import TPCDS_TABLES

QUERY_DIR = Path("queries/tpcds/queries")
ANSWER_BASE = Path("queries/tpcds/answers")


def generate_base(scale_factor: float, data_dir: Path) -> None:
    """Populate the 24 TPC-DS tables via dsdgen and write one Parquet file per table."""
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating TPC-DS data at scale factor {scale_factor} via the tpcds extension...")

    with duckdb.connect() as conn:
        conn.execute("INSTALL tpcds; LOAD tpcds;")
        conn.execute(f"CALL dsdgen(sf={scale_factor})")
        for table in TPCDS_TABLES:
            out = (data_dir / f"{table}.parquet").absolute()
            conn.execute(f"COPY {table} TO '{out}' (FORMAT PARQUET)")
            row_count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table} → {out} ({row_count:,} rows)")

    print("Done.")


def extract_queries(query_dir: Path = QUERY_DIR) -> None:
    """Write the 99 TPC-DS query texts to query_dir/qNN.sql."""
    query_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing TPC-DS query files → {query_dir}")

    with duckdb.connect() as conn:
        conn.execute("INSTALL tpcds; LOAD tpcds;")
        rows = conn.execute(
            "SELECT query_nr, query FROM tpcds_queries() ORDER BY query_nr"
        ).fetchall()

    for query_nr, sql in rows:
        out = query_dir / f"q{query_nr:02d}.sql"
        out.write_text(sql.strip() + "\n")
    print(f"  wrote {len(rows)} query files")


def generate_answers(
    scale_factor: int,
    data_dir: Path,
    query_dir: Path = QUERY_DIR,
    answer_base: Path = ANSWER_BASE,
) -> None:
    """
    Run every query file against the base Parquet tables and write the results as
    CSV reference answers. Mirrors setup.generate_answers for TPC-H.
    """
    for table in TPCDS_TABLES:
        if not (data_dir / f"{table}.parquet").exists():
            print(
                f"error: {data_dir / f'{table}.parquet'} not found. "
                f"Generate base data first: python -m setup.generate_tpcds --sf {scale_factor}",
                file=sys.stderr,
            )
            sys.exit(1)

    query_files = sorted(query_dir.glob("q*.sql"))
    if not query_files:
        print(f"error: no query files in {query_dir}. Run extract_queries first.", file=sys.stderr)
        sys.exit(1)

    answer_dir = answer_base / f"sf{scale_factor}"
    answer_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating TPC-DS answers for sf{scale_factor} → {answer_dir}")

    with duckdb.connect() as conn:
        for table in TPCDS_TABLES:
            src = (data_dir / f"{table}.parquet").absolute()
            conn.execute(
                f"CREATE TABLE {table} AS "
                f"SELECT * FROM read_parquet('{src}', hive_partitioning=false)"
            )

        for qfile in query_files:
            # Strip the trailing ';' so the query can be wrapped in COPY (...).
            query_sql = qfile.read_text().strip().rstrip(";")
            out = answer_dir / f"{qfile.stem}.csv"
            conn.execute(f"COPY ({query_sql}) TO '{out}' (FORMAT CSV, HEADER)")
        print(f"  wrote {len(query_files)} answer files")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sf", type=int, default=1, help="TPC-DS scale factor")
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="Base data directory (default: data/tpcds/sf=<sf>)",
    )
    parser.add_argument(
        "--no-answers", action="store_true",
        help="Skip generating the TPC-DS answer files after the base tables",
    )
    args = parser.parse_args()
    data_dir = args.data_dir if args.data_dir is not None else Path("data/tpcds") / f"sf={args.sf}"

    generate_base(scale_factor=args.sf, data_dir=data_dir)
    extract_queries()
    if not args.no_answers:
        generate_answers(scale_factor=args.sf, data_dir=data_dir)
