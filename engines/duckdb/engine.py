from __future__ import annotations

from typing import Any

import duckdb

from catalogs.base import Catalog
from catalogs.ducklake import DuckLakeCatalog
from engines.base import Engine
from engines.duckdb.catalog_adapters import (
    CATALOG_ALIAS,
    attach_catalog,
)


class _DuckDBCursorEngine:
    """
    Lightweight cursor-backed engine for concurrent throughput streams.
    Shares the parent connection's attached catalog but has its own transaction
    and USE context, making it safe to use from multiple threads simultaneously.
    """

    def __init__(self, cursor: duckdb.DuckDBPyConnection, catalog_alias: str, use_transactions: bool):
        self._cursor = cursor
        self._catalog_alias = catalog_alias
        self._use_transactions = use_transactions
        self._current_namespace: str | None = None

    def _use(self, namespace: str) -> None:
        if namespace != self._current_namespace:
            self._cursor.execute(f"USE {self._catalog_alias}.{namespace}")
            self._current_namespace = namespace

    def run_query(self, sql: str, namespace: str) -> tuple[list[tuple], list[str], int]:
        self._use(namespace)
        if self._use_transactions:
            self._cursor.execute("BEGIN TRANSACTION READ ONLY")
        rel = self._cursor.execute(sql)
        # Fetch before COMMIT: another execute() on the same connection invalidates
        # this result cursor, so fetching after COMMIT would return 0 rows.
        rows = rel.fetchall()
        col_names = [desc[0] for desc in rel.description]
        if self._use_transactions:
            self._cursor.execute("COMMIT")
        return rows, col_names, len(rows)

    def run_rf1(self, data_dir: Path, namespace: str, set_n: int) -> None:
        self._use(namespace)
        orders = str((data_dir / f"orders_u{set_n}.parquet").absolute())
        lineitem = str((data_dir / f"lineitem_u{set_n}.parquet").absolute())
        if self._use_transactions:
            self._cursor.begin()
        self._cursor.execute(f"INSERT INTO orders SELECT * FROM read_parquet('{orders}', hive_partitioning=false)")
        self._cursor.execute(f"INSERT INTO lineitem SELECT * FROM read_parquet('{lineitem}', hive_partitioning=false)")
        if self._use_transactions:
            self._cursor.commit()

    def run_rf2(self, data_dir: Path, namespace: str, set_n: int) -> None:
        self._use(namespace)
        delete_keys = str((data_dir / f"delete_set_{set_n}.parquet").absolute())
        if self._use_transactions:
            self._cursor.begin()
        self._cursor.execute(
            f"DELETE FROM orders WHERE o_orderkey IN "
            f"(SELECT o_orderkey FROM read_parquet('{delete_keys}'))"
        )
        self._cursor.execute(
            f"DELETE FROM lineitem WHERE l_orderkey IN "
            f"(SELECT o_orderkey FROM read_parquet('{delete_keys}'))"
        )
        if self._use_transactions:
            self._cursor.commit()

    def fork_for_stream(self) -> "_DuckDBCursorEngine":
        return _DuckDBCursorEngine(self._cursor.cursor(), self._catalog_alias, self._use_transactions)


class DuckDBEngine(Engine):
    def __init__(self, catalog: Catalog):
        super().__init__(catalog)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._catalog_alias: str | None = None
        self._current_namespace: str | None = None
        # Only DuckLake supports DuckDB-native transactions. Iceberg catalogs
        # (s3tables, local) go through the Iceberg extension which doesn't.
        self._use_transactions: bool = isinstance(catalog, DuckLakeCatalog)

    def version(self) -> str:
        return duckdb.__version__

    def supports_compaction(self, catalog: Catalog) -> bool:
        # DuckDB compacts DuckLake (rewrite + merge_adjacent_files). Its Iceberg extension
        # has no compaction step, so Iceberg catalogs are not compactable on DuckDB.
        return isinstance(catalog, DuckLakeCatalog)

    def _use(self, namespace: str) -> None:
        if namespace != self._current_namespace:
            self._conn.execute(f"USE {self._catalog_alias}.{namespace}")
            self._current_namespace = namespace

    def setup(self, tables: list[str]) -> None:
        self._conn = duckdb.connect()
        self._catalog_alias = attach_catalog(self._conn, self.catalog)
        # Fresh connection has no USE context. Clear the cached namespace so the next
        # _use() actually emits USE — otherwise a re-setup() within the same namespace
        # (e.g. the compaction sweep, which tears down and rebuilds per depth) would keep
        # a stale value and skip USE, leaving unqualified table names unresolved.
        self._current_namespace = None

    def run_query(self, sql: str, namespace: str) -> tuple[list[tuple], list[str], int]:
        assert self._conn is not None, "Call setup() before run_query()"
        self._use(namespace)
        if self._use_transactions:
            self._conn.execute("BEGIN TRANSACTION READ ONLY")
        relation = self._conn.execute(sql)
        # Fetch before COMMIT: another execute() on the same connection invalidates
        # this result cursor, so fetching after COMMIT would return 0 rows.
        rows = relation.fetchall()
        col_names = [desc[0] for desc in relation.description]
        if self._use_transactions:
            self._conn.execute("COMMIT")
        return rows, col_names, len(rows)

    def run_rf1(self, data_dir: Path, namespace: str, set_n: int) -> None:
        assert self._conn is not None, "Call setup() before run_rf1()"
        self._use(namespace)
        orders = str((data_dir / f"orders_u{set_n}.parquet").absolute())
        lineitem = str((data_dir / f"lineitem_u{set_n}.parquet").absolute())
        if self._use_transactions:
            self._conn.begin()
        self._conn.execute(f"INSERT INTO orders SELECT * FROM read_parquet('{orders}', hive_partitioning=false)")
        self._conn.execute(f"INSERT INTO lineitem SELECT * FROM read_parquet('{lineitem}', hive_partitioning=false)")
        if self._use_transactions:
            self._conn.commit()

    def run_rf2(self, data_dir: Path, namespace: str, set_n: int) -> None:
        assert self._conn is not None, "Call setup() before run_rf2()"
        self._use(namespace)
        delete_keys = str((data_dir / f"delete_set_{set_n}.parquet").absolute())
        if self._use_transactions:
            self._conn.begin()
        self._conn.execute(
            f"DELETE FROM orders WHERE o_orderkey IN "
            f"(SELECT o_orderkey FROM read_parquet('{delete_keys}'))"
        )
        self._conn.execute(
            f"DELETE FROM lineitem WHERE l_orderkey IN "
            f"(SELECT o_orderkey FROM read_parquet('{delete_keys}'))"
        )
        if self._use_transactions:
            self._conn.commit()

    def load_staging(self, round_dir: Path, namespace: str) -> None:
        """
        Load a data-maintenance round's Parquet files into temporary staging tables.

        Each file becomes a TEMP table named after its stem, so the maintenance SQL can
        reference it unqualified alongside the catalog's fact/dimension tables. The delete
        range files are renamed to dm_delete / dm_inventory_delete (avoiding the DELETE
        keyword) to match the DF_ maintenance SQL.
        """
        assert self._conn is not None, "Call setup() before load_staging()"
        if not self.catalog.engine_writable:
            raise NotImplementedError(
                "data maintenance requires an engine-writable catalog "
                "(DuckLake, S3Tables, Glue, or an Iceberg REST catalog)"
            )
        self._use(namespace)
        rename = {"delete": "dm_delete", "inventory_delete": "dm_inventory_delete"}
        for pq in sorted(round_dir.glob("*.parquet")):
            table = rename.get(pq.stem, pq.stem)
            query = f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT * FROM read_parquet('{pq.absolute()}', hive_partitioning=false)"
            self._conn.execute(query)

    # Target size (bytes) for compacted output files. Small data files below this are
    # candidates for consolidation; ~256 MB is a common lakehouse target file size.
    _COMPACT_TARGET_BYTES = 256 * 1024 * 1024

    def optimize(self, namespace: str) -> None:
        """
        Compact the catalog. This is the timed unit of the compaction benchmark.

        Two steps, because data-maintenance produces both small files and delete files:
          1. rewrite_data_files(delete_threshold => 0) — rewrites every file carrying
             deletes, applying them and consolidating (merge_adjacent_files skips files
             with pending deletes, so this is required to compact a maintained table).
          2. merge_adjacent_files(max_file_size => target) — bin-packs the remaining small
             delete-free files up to the target size.

        DuckLake only — DuckDB's Iceberg extension has no compaction/rewrite step yet, so
        compaction on an Iceberg catalog raises (gated by supports_compaction()).
        Snapshot expiry / orphan cleanup (space reclamation) are separate, via reclaim().
        """
        assert self._conn is not None, "Call setup() before optimize()"
        if not self.supports_compaction(self.catalog):
            raise NotImplementedError(
                "compaction is not supported for this catalog on DuckDB — DuckDB's Iceberg "
                "extension has no compaction step yet (only DuckLake supports it)"
            )
        alias = self._catalog_alias
        self._conn.execute(f"CALL ducklake_rewrite_data_files('{alias}', delete_threshold => 0.0)")
        self._conn.execute(
            f"CALL ducklake_merge_adjacent_files('{alias}', max_file_size => {self._COMPACT_TARGET_BYTES})"
        )

    def reclaim(self, namespace: str) -> None:
        """Expire old snapshots and delete orphaned files (post-compaction reclamation)."""
        assert self._conn is not None, "Call setup() before reclaim()"
        if not self.supports_compaction(self.catalog):
            raise NotImplementedError("reclaim is only implemented for the DuckLake catalog")
        self._conn.execute(
            f"CALL ducklake_expire_snapshots('{self._catalog_alias}', older_than => now())"
        )
        self._conn.execute(f"CALL ducklake_cleanup_old_files('{self._catalog_alias}', older_than => now())")

    def table_stats(self, namespace: str) -> dict[str, int]:
        """
        Aggregate catalog health metrics: data-file count/bytes and delete-file count/bytes.
        Used to characterize the table state before/after compaction. DuckLake only —
        the file-count catalog view has no DuckDB Iceberg equivalent yet.
        """
        assert self._conn is not None, "Call setup() before table_stats()"
        if not self.supports_compaction(self.catalog):
            raise NotImplementedError("table_stats is only implemented for the DuckLake catalog")
        row = self._conn.execute(f"""
            SELECT coalesce(sum(file_count), 0), coalesce(sum(file_size_bytes), 0),
                   coalesce(sum(delete_file_count), 0), coalesce(sum(delete_file_size_bytes), 0)
            FROM ducklake_table_info('{self._catalog_alias}')
        """).fetchone()
        return {
            "file_count": int(row[0]),
            "file_size_bytes": int(row[1]),
            "delete_file_count": int(row[2]),
            "delete_file_size_bytes": int(row[3]),
        }

    def run_maintenance(self, sql: str, namespace: str) -> None:
        """Execute one data-maintenance function (a multi-statement SQL script)."""
        assert self._conn is not None, "Call setup() before run_maintenance()"
        self._use(namespace)
        # Strip line comments first so a ';' inside a -- comment doesn't split a statement.
        stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        for stmt in (s.strip() for s in stripped.split(";")):
            if stmt:
                self._conn.execute(stmt)

    def fork_for_stream(self) -> _DuckDBCursorEngine:
        assert self._conn is not None, "Call setup() before fork_for_stream()"
        return _DuckDBCursorEngine(self._conn.cursor(), self._catalog_alias, self._use_transactions)

    def teardown(self) -> None:
        if self._conn is not None:
            if self._catalog_alias:
                try:
                    self._conn.execute(f"DETACH {self._catalog_alias};")
                except Exception:
                    pass
            self._conn.close()
            self._conn = None
            self._catalog_alias = None
            self._current_namespace = None
