from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from catalogs.base import Catalog

_ALL_BENCHMARKS = frozenset({"load", "analytical", "power", "throughput", "composite", "maintenance", "compaction"})


class Engine(ABC):
    # Subclasses can narrow this to restrict which benchmarks are valid for this engine.
    SUPPORTED_BENCHMARKS: frozenset[str] = _ALL_BENCHMARKS

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def version(self) -> str:
        """Engine/runtime version string, recorded with the results."""
        return "unknown"

    @abstractmethod
    def setup(self, tables: list[str]) -> None:
        """
        Initialize engine connection/session and attach catalog.

        `tables` is the active suite's table list — available to engines that must
        materialize per-table views or otherwise pre-register tables at setup time.
        """

    @abstractmethod
    def run_query(self, sql: str, namespace: str) -> tuple[list[tuple], list[str], int]:
        """
        Execute sql against the catalog namespace.
        Returns (rows, column_names, row_count).
        """

    @abstractmethod
    def teardown(self) -> None:
        """Close connections and release resources."""

    @abstractmethod
    def run_rf1(self, data_dir: Path, namespace: str, set_n: int) -> None:
        """
        RF1: insert refresh rows from local parquet files into the live tables.
        Reads orders_u{set_n}.parquet and lineitem_u{set_n}.parquet from data_dir.
        """

    @abstractmethod
    def run_rf2(self, data_dir: Path, namespace: str, set_n: int) -> None:
        """
        RF2: delete rows from the live tables using local parquet delete keys.
        Reads delete_set_{set_n}.parquet from data_dir.
        """

    def load_staging(self, round_dir: Path, namespace: str) -> None:
        """Load a TPC-DS data-maintenance round's Parquet files into staging tables."""
        raise NotImplementedError(f"{type(self).__name__} does not support data maintenance")

    def run_maintenance(self, sql: str, namespace: str) -> None:
        """Execute one TPC-DS data-maintenance function against the catalog."""
        raise NotImplementedError(f"{type(self).__name__} does not support data maintenance")

    def optimize(self, namespace: str) -> None:
        """Compact the catalog (merge small data files). The compaction benchmark times this."""
        raise NotImplementedError(f"{type(self).__name__} does not support compaction")

    def table_stats(self, namespace: str) -> dict[str, int]:
        """Return catalog health metrics (file/delete-file counts and sizes)."""
        raise NotImplementedError(f"{type(self).__name__} does not support table stats")

    def fork_for_stream(self) -> "Engine":
        """
        Return a lightweight engine suitable for running in a concurrent thread.

        DuckDB: returns a cursor-backed runner that shares the parent connection's
        attached catalog but has independent transaction and USE state.
        Spark: returns self — SparkSession is thread-safe for concurrent job submission.
        """
        return self

    def run_query_file(self, path: Path, namespace: str) -> tuple[list[tuple], list[str], int]:
        return self.run_query(path.read_text(), namespace)
