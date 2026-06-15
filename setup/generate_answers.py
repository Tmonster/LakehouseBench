"""
Generate TPC-H query answers (expected results) for a given scale factor.

Loads the base tables from data/sf={scale_factor}/{table}.parquet into an
in-memory DuckDB, runs the 22 query files from queries/tpch/queries/, and
writes each result to queries/tpch/answers/sf{X}/qNN.csv.

These CSVs are the reference answers used by benchmarks/analytical.py to
verify query correctness.

Usage:
    python -m setup.generate_answers --sf 1
    python -m setup.generate_answers --sf 10 --data-dir /tmp/tpch
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

TPCH_TABLES = [
    "customer", "lineitem", "nation", "orders",
    "part", "partsupp", "region", "supplier",
]

QUERY_DIR = Path("queries/tpch/queries")
ANSWER_BASE = Path("queries/tpch/answers")


def generate_answers(scale_factor: int, data_dir: Path) -> None:
    for table in TPCH_TABLES:
        if not (data_dir / f"{table}.parquet").exists():
            print(
                f"error: {data_dir / f'{table}.parquet'} not found. "
                f"Generate the data first with:\n"
                f"  python -m setup.generate_data --sf {scale_factor}",
                file=sys.stderr,
            )
            sys.exit(1)

    answer_dir = ANSWER_BASE / f"sf{scale_factor}"
    answer_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating TPC-H answers for sf{scale_factor} → {answer_dir}")

    query_files = sorted(QUERY_DIR.glob("q*.sql"))
    if not query_files:
        print(f"error: no query files found in {QUERY_DIR}.", file=sys.stderr)
        sys.exit(1)

    with duckdb.connect() as conn:
        # Load base tables so the queries resolve against this data.
        for table in TPCH_TABLES:
            src = data_dir / f"{table}.parquet"
            conn.execute(f"CREATE TABLE {table} AS SELECT * FROM '{src}'")

        # Generate answers from the exact queries the benchmark runs, so the two
        # always stay in sync (e.g. when text columns are dropped to make results
        # independent of the data generator's string output).
        for qfile in query_files:
            # Strip the trailing ';' so the query can be wrapped in COPY (...).
            query_sql = qfile.read_text().strip().rstrip(";")
            out = answer_dir / f"{qfile.stem}.csv"
            conn.execute(f"COPY ({query_sql}) TO '{out}' (FORMAT CSV, HEADER)")
            print(f"  {qfile.stem} → {out}")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sf", type=int, default=1, help="TPC-H scale factor")
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="Base data directory (default: data/sf=<sf>)",
    )
    args = parser.parse_args()
    data_dir = args.data_dir if args.data_dir is not None else Path("data") / f"sf={args.sf}"
    generate_answers(scale_factor=args.sf, data_dir=data_dir)
