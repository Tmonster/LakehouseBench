"""
Benchmark suite descriptor.

A Suite parameterizes everything that used to be hardcoded to TPC-H: the table
list, the data directory layout, and the query/answer directories. This lets the
same catalogs, engines, and benchmarks run either TPC-H or TPC-DS by passing the
active suite through instead of importing a module-level TPCH_TABLES constant.

Layout per suite:
  tpch   -> data/sf=<N>/*.parquet          queries/tpch/{queries,answers}
  tpcds  -> data/tpcds/sf=<N>/*.parquet     queries/tpcds/{queries,answers}

TPC-H keeps its historical data/sf=<N> layout (no suite subdirectory) so existing
generated data and results paths are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# TPC-H: 8 tables.
TPCH_TABLES: tuple[str, ...] = (
    "customer", "lineitem", "nation", "orders",
    "part", "partsupp", "region", "supplier",
)

# TPC-DS: 24 tables (7 fact, 17 dimension). Order matches the DuckDB tpcds
# extension's dsdgen output.
TPCDS_TABLES: tuple[str, ...] = (
    "call_center", "catalog_page", "catalog_returns", "catalog_sales",
    "customer", "customer_address", "customer_demographics", "date_dim",
    "household_demographics", "income_band", "inventory", "item",
    "promotion", "reason", "ship_mode", "store",
    "store_returns", "store_sales", "time_dim", "warehouse",
    "web_page", "web_returns", "web_sales", "web_site",
)


@dataclass(frozen=True)
class Suite:
    name: str
    tables: tuple[str, ...]
    data_root: Path       # base data dir; per-SF dir is data_root/sf=<N>
    query_dir: Path       # directory of qNN.sql files
    answer_base: Path     # base answers dir; per-SF dir is answer_base/sf<N>

    def data_dir(self, scale_factor: int) -> Path:
        return self.data_root / f"sf={scale_factor}"

    def answer_dir(self, scale_factor: int) -> Path:
        return self.answer_base / f"sf{scale_factor}"

    @property
    def query_count(self) -> int:
        return len(sorted(self.query_dir.glob("q*.sql")))


SUITES: dict[str, Suite] = {
    "tpch": Suite(
        name="tpch",
        tables=TPCH_TABLES,
        data_root=Path("data"),
        query_dir=Path("queries/tpch/queries"),
        answer_base=Path("queries/tpch/answers"),
    ),
    "tpcds": Suite(
        name="tpcds",
        tables=TPCDS_TABLES,
        data_root=Path("data/tpcds"),
        query_dir=Path("queries/tpcds/queries"),
        answer_base=Path("queries/tpcds/answers"),
    ),
}


def get_suite(name: str) -> Suite:
    try:
        return SUITES[name]
    except KeyError:
        raise ValueError(f"Unknown suite: {name!r} (choose from {sorted(SUITES)})")
