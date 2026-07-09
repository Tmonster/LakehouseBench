from __future__ import annotations

from pathlib import Path

from catalogs.base import Catalog
from engines.base import Engine
from engines.spark.catalog_adapters import spark_catalog_alias, spark_config


class SparkEngine(Engine):
    def __init__(self, catalog: Catalog):
        super().__init__(catalog)
        self._spark = None
        self._catalog_alias: str | None = None

    def version(self) -> str:
        return self._spark.version if self._spark is not None else "unknown"

    def setup(self, tables: list[str]) -> None:
        import os
        import sys
        from pyspark.sql import SparkSession

        from catalogs.ducklake import DuckLakeCatalog
        if isinstance(self.catalog, DuckLakeCatalog):
            raise NotImplementedError(
                "DuckLake catalog is DuckDB-only and cannot be used with the Spark engine."
            )

        # Ensure PySpark workers use the same Python as the calling process (venv-safe)
        os.environ["PYSPARK_PYTHON"] = sys.executable

        builder = SparkSession.builder.appName("iceberg-benchmark")
        for key, val in spark_config(self.catalog).items():
            builder = builder.config(key, val)

        self._spark = builder.getOrCreate()
        self._catalog_alias = spark_catalog_alias(self.catalog)

    def run_query(self, sql: str, namespace: str) -> tuple[list[tuple], list[str], int]:
        assert self._spark is not None, "Call setup() before run_query()"
        # Set search path so unqualified table names in TPC-H SQL resolve correctly
        self._spark.sql(f"USE {self._catalog_alias}.{namespace}")
        df = self._spark.sql(sql)
        col_names = df.columns
        # collect() triggers actual execution — this is what we're timing
        rows = [tuple(row) for row in df.collect()]
        return rows, col_names, len(rows)

    def run_rf1(self, data_dir: Path, namespace: str, set_n: int) -> None:
        assert self._spark is not None, "Call setup() before run_rf1()"
        orders = str((data_dir / f"orders_u{set_n}.parquet").absolute())
        lineitem = str((data_dir / f"lineitem_u{set_n}.parquet").absolute())
        (self._spark.read.parquet(orders)
            .writeTo(f"{self._catalog_alias}.{namespace}.orders")
            .append())
        (self._spark.read.parquet(lineitem)
            .writeTo(f"{self._catalog_alias}.{namespace}.lineitem")
            .append())

    def run_rf2(self, data_dir: Path, namespace: str, set_n: int) -> None:
        assert self._spark is not None, "Call setup() before run_rf2()"
        delete_keys = str((data_dir / f"delete_set_{set_n}.parquet").absolute())
        # Register as a temp view so it's referenceable in DELETE SQL
        self._spark.read.parquet(delete_keys).createOrReplaceTempView("_rf2_delete_keys")
        self._spark.sql(f"USE {self._catalog_alias}.{namespace}")
        self._spark.sql(
            "DELETE FROM orders WHERE o_orderkey IN "
            "(SELECT o_orderkey FROM _rf2_delete_keys)"
        )
        self._spark.sql(
            "DELETE FROM lineitem WHERE l_orderkey IN "
            "(SELECT o_orderkey FROM _rf2_delete_keys)"
        )

    def supports_compaction(self, catalog: Catalog) -> bool:
        # Spark compacts Iceberg via rewrite_data_files. DuckLake is DuckDB-only (rejected
        # in setup()), so any catalog reaching Spark here is Iceberg-backed.
        return catalog.catalog_info().get("table_format") == "iceberg"

    def load_staging(self, round_dir: Path, namespace: str) -> None:
        """Register a DM round's Parquet files as session temp views (delete → dm_delete)."""
        assert self._spark is not None, "Call setup() before load_staging()"
        rename = {"delete": "dm_delete", "inventory_delete": "dm_inventory_delete"}
        for pq in sorted(round_dir.glob("*.parquet")):
            name = rename.get(pq.stem, pq.stem)
            self._spark.read.parquet(str(pq.absolute())).createOrReplaceTempView(name)

    def run_maintenance(self, sql: str, namespace: str) -> None:
        """Execute one data-maintenance function (a multi-statement SQL script)."""
        assert self._spark is not None, "Call setup() before run_maintenance()"
        self._spark.sql(f"USE {self._catalog_alias}.{namespace}")
        # Strip line comments so a ';' inside a -- comment doesn't split a statement.
        stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        for stmt in (s.strip() for s in stripped.split(";")):
            if stmt:
                self._spark.sql(stmt)

    def run_delete_fact(self, statements: list[str], namespace: str) -> None:
        """
        Run each Delete-Fact statement once per date range staged in dm_delete.

        The (date1, date2) windows are collected from the dm_delete temp view and inlined
        as DATE literals — Spark's positional-arg binding (`?`) does not propagate into the
        subquery of a DELETE ... WHERE col IN (SELECT ...), leaving the params unbound
        (UNBOUND_SQL_PARAMETER). The windows come from our own generated parquet, so
        literal substitution is safe here. This still avoids the date_dim × dm_delete join
        that Spark planned as an expensive broadcast/semijoin over the full fact table.
        """
        assert self._spark is not None, "Call setup() before run_delete_fact()"
        self._spark.sql(f"USE {self._catalog_alias}.{namespace}")
        ranges = [(r["date1"], r["date2"]) for r in self._spark.sql("SELECT date1, date2 FROM dm_delete").collect()]
        for stmt in statements:
            for date1, date2 in ranges:
                bound = stmt.replace("?", f"DATE '{date1}'", 1).replace("?", f"DATE '{date2}'", 1)
                self._spark.sql(bound)

    # Target compacted file size (bytes) — matches the DuckDB engine's 256 MB target.
    _COMPACT_TARGET_BYTES = 256 * 1024 * 1024

    def _namespace_tables(self, namespace: str) -> list[str]:
        rows = self._spark.sql(f"SHOW TABLES IN {self._catalog_alias}.{namespace}").collect()
        # SHOW TABLES yields (namespace, tableName, isTemporary); skip temp views (staging).
        return [r["tableName"] for r in rows if not r["isTemporary"]]

    def optimize(self, namespace: str) -> None:
        """Compact each table in the namespace (Iceberg rewrite). The compaction benchmark times this."""
        assert self._spark is not None, "Call setup() before optimize()"
        if not self.supports_compaction(self.catalog):
            raise NotImplementedError("compaction is only supported for Iceberg catalogs on Spark")
        cat = self._catalog_alias
        for t in self._namespace_tables(namespace):
            fq = f"{namespace}.{t}"
            # Rewrite data files carrying deletes (threshold 1 = any delete file) and
            # bin-pack small files up to the target size, then consolidate delete files.
            self._spark.sql(
                f"CALL {cat}.system.rewrite_data_files(table => '{fq}', options => map("
                f"'delete-file-threshold','1','min-input-files','2',"
                f"'target-file-size-bytes','{self._COMPACT_TARGET_BYTES}'))"
            )
            self._spark.sql(f"CALL {cat}.system.rewrite_position_delete_files(table => '{fq}')")

    def table_stats(self, namespace: str) -> dict[str, int]:
        """Aggregate data/delete file counts + bytes across the namespace's Iceberg tables."""
        assert self._spark is not None, "Call setup() before table_stats()"
        if not self.supports_compaction(self.catalog):
            raise NotImplementedError("table_stats is only supported for Iceberg catalogs on Spark")
        cat = self._catalog_alias
        fc = fb = dc = db = 0
        for t in self._namespace_tables(namespace):
            files = f"{cat}.{namespace}.{t}.files"
            # content: 0 = data file, 1/2 = position/equality delete file.
            r = self._spark.sql(
                f"SELECT count(*) c, coalesce(sum(file_size_in_bytes),0) b FROM {files} WHERE content = 0"
            ).collect()[0]
            fc += r["c"]; fb += r["b"]
            r = self._spark.sql(
                f"SELECT count(*) c, coalesce(sum(file_size_in_bytes),0) b FROM {files} WHERE content <> 0"
            ).collect()[0]
            dc += r["c"]; db += r["b"]
        return {
            "file_count": int(fc), "file_size_bytes": int(fb),
            "delete_file_count": int(dc), "delete_file_size_bytes": int(db),
        }

    def teardown(self) -> None:
        if self._spark is not None:
            self._spark.stop()
            self._spark = None
            self._catalog_alias = None
