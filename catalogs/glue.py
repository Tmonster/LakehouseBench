"""
AWS Glue Iceberg catalog — accessed through DuckDB's Iceberg extension with
ENDPOINT_TYPE 'GLUE'. Unlike S3 Tables, Glue does not manage table storage, so
every CREATE TABLE must carry an explicit 'location'. Each table is given its
own subdirectory under `base_location` (Iceberg tables cannot share a location).

DuckDB-only: Spark and other engines raise NotImplementedError at setup().

Config:
  type: glue
  table_format: iceberg
  region: eu-central-1
  account_id: "840140254803"                 # Glue catalog id (the ATTACH target)
  namespace: lakehouse_benchmarking          # = Glue database name
  base_location: s3://my-bucket/lakehouse/   # per-table dirs created beneath this
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from catalogs.base import Catalog, CatalogConfig

TPCH_TABLES = [
    "customer", "lineitem", "nation", "orders",
    "part", "partsupp", "region", "supplier",
]


class GlueCatalog(Catalog):
    def __init__(self, config: CatalogConfig):
        super().__init__(config)
        self.region: str = config.extra["region"]
        self.account_id: str = config.extra["account_id"]
        # Strip trailing slash so per-table joins produce a single separator.
        self.base_location: str = config.extra["base_location"].rstrip("/")

    def provision(self, namespace: str, data_dir: Path) -> None:
        from setup.write_tables import write_tpch_tables
        write_tpch_tables(catalog=self, namespace=namespace, data_dir=data_dir)

    def teardown(self, namespace: str) -> None:
        import duckdb
        from engines.duckdb.catalog_adapters import attach_catalog

        with duckdb.connect() as conn:
            alias = attach_catalog(conn, self)
            for table in TPCH_TABLES:
                try:
                    conn.execute(f"DROP TABLE IF EXISTS {alias}.{namespace}.{table}")
                except Exception:
                    pass
            try:
                conn.execute(f"DROP SCHEMA IF EXISTS {alias}.{namespace}")
            except Exception:
                pass

    def table_ref(self, table: str, namespace: str | None = None) -> str:
        ns = namespace or self.config.namespace
        return f"{ns}.{table}"

    def table_create_options(self, table: str, namespace: str) -> dict[str, str]:
        # Config-driven Iceberg table properties (from base) plus Glue's required location.
        opts = super().table_create_options(table, namespace)
        opts["location"] = f"{self.base_location}/{namespace}/{table}/"
        return opts

    def catalog_info(self) -> dict[str, str | None]:
        service = "aws-glue"
        return {
            "table_format": self.config.extra.get("table_format", "iceberg"),
            "catalog_service": service,
            "catalog_name": self.config.extra.get("catalog_name", service),
            "catalog_region": self.region,
            "storage_service": "s3",
            "storage_region": self.region,
        }

    def connection_properties(self) -> dict[str, Any]:
        return {
            "type": "glue",
            "account_id": self.account_id,
            "region": self.region,
            "base_location": self.base_location,
        }
