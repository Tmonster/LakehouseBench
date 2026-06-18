"""
DuckLake catalog — DuckDB-native transactional table format.
Uses a local SQLite metadata file (.ducklake) plus a data path for Parquet files.
The data path may be a local directory or an object-store URI (e.g. s3://...).

DuckDB-only: Spark and other engines raise NotImplementedError at setup().

Config (local data):
  type: ducklake
  namespace: benchmarks
  metadata_path: ducklake/tpch.ducklake   # SQLite metadata file (created if absent)
  data_path: ducklake/files               # local directory for DuckLake Parquet files

Config (data on S3 — metadata stays local):
  type: ducklake
  namespace: benchmarks
  metadata_path: ducklake/tpch.ducklake
  data_path: s3://my-bucket/ducklake/      # S3 prefix; credentials via AWS provider chain
  region: eu-central-1
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from catalogs.base import Catalog, CatalogConfig

TPCH_TABLES = [
    "customer", "lineitem", "nation", "orders",
    "part", "partsupp", "region", "supplier",
]

_ALIAS = "ducklake_catalog"


def is_remote_data_path(data_path: str) -> bool:
    """A data path with a URI scheme (e.g. s3://, gs://) lives in an object store."""
    return "://" in data_path


# URI scheme -> storage_service label recorded in results.
_SCHEME_TO_STORAGE = {
    "s3": "s3",
    "gs": "gcs", "gcs": "gcs",
    "az": "azure", "azure": "azure", "abfs": "azure", "abfss": "azure",
}


def _storage_service(data_path: str) -> str:
    if not is_remote_data_path(data_path):
        return "local"
    scheme = data_path.split("://", 1)[0].lower()
    return _SCHEME_TO_STORAGE.get(scheme, scheme)


class DuckLakeCatalog(Catalog):
    def __init__(self, config: CatalogConfig):
        super().__init__(config)
        self.metadata_path = Path(config.extra.get("metadata_path", "ducklake/tpch.ducklake"))
        # Kept as a raw string: Path() would mangle an s3:// URI ("s3://" -> "s3:/").
        self.data_path = config.extra.get("data_path", "ducklake/files")
        self.region = config.extra.get("region")

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
        return f"{_ALIAS}.{ns}.{table}"

    def catalog_info(self) -> dict[str, str | None]:
        storage = _storage_service(self.data_path)
        service = "ducklake"
        return {
            "table_format": self.config.extra.get("table_format", "ducklake"),
            "catalog_service": service,
            "catalog_name": self.config.extra.get("catalog_name", service),
            "catalog_region": None,        # metadata is a local SQLite file, not a hosted service
            "storage_service": storage,
            "storage_region": self.region if storage == "s3" else None,
        }

    def connection_properties(self) -> dict[str, Any]:
        # Local data paths are made absolute; remote (s3://...) paths pass through verbatim.
        data_path = self.data_path if is_remote_data_path(self.data_path) else str(Path(self.data_path).absolute())
        return {
            "type": "ducklake",
            "metadata_path": str(self.metadata_path.absolute()),
            "data_path": data_path,
            "region": self.region,
        }
