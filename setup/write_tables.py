"""
Write a benchmark suite's Parquet files into a catalog.
Called by Catalog.provision() — not intended to be run directly.

All supported catalogs (ducklake, s3tables, glue, iceberg_rest) are written through
DuckDB ATTACH + CREATE TABLE AS SELECT via read_parquet().
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from engines.duckdb.catalog_adapters import attach_catalog

if TYPE_CHECKING:
    from catalogs.base import Catalog


def write_tables(catalog: "Catalog", namespace: str, data_dir: Path, tables: list[str]) -> None:
    _write_via_duckdb(catalog, namespace, data_dir, tables)



def _write_via_duckdb(catalog: "Catalog", namespace: str, data_dir: Path, tables: list[str]) -> None:
    with duckdb.connect() as conn:
        alias = attach_catalog(conn, catalog)
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{namespace}")
        for table_name in tables:
            parquet_path = (data_dir / f"{table_name}.parquet").absolute()
            if not parquet_path.exists():
                raise FileNotFoundError(
                    f"Missing {parquet_path}. Run `python -m setup.generate_data` first."
                )
            conn.execute(f"DROP TABLE IF EXISTS {alias}.{namespace}.{table_name}")
            # Some catalogs (e.g. Glue) require per-table options such as 'location',
            # emitted as a WITH (...) clause; most return {} and get a plain CTAS.
            opts = catalog.table_create_options(table_name, namespace)
            with_clause = ""
            if opts:
                pairs = ", ".join(f"'{k}'='{v}'" for k, v in opts.items())
                with_clause = f"WITH ({pairs})"
            conn.execute(f"""
                CREATE TABLE {alias}.{namespace}.{table_name}
                {with_clause}
                AS SELECT * FROM read_parquet('{parquet_path}', hive_partitioning=false);
            """)
            row_count = conn.execute(
                f"SELECT count(*) FROM {alias}.{namespace}.{table_name}"
            ).fetchone()[0]
            print(f"  {namespace}.{table_name}: {row_count:,} rows written")
