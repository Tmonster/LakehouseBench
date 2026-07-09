"""
Self-hosted Iceberg REST catalog (e.g. the Iceberg REST fixture, Lakekeeper, Polaris,
Nessie) with object storage behind an S3-compatible endpoint such as MinIO.

DuckDB attaches a REST catalog with full write support, so data-maintenance INSERT/DELETE
runs through the engine — this is the local-friendly, DuckDB-writable Iceberg option.
Compaction is still unsupported (DuckDB's Iceberg extension has no rewrite step).

Mirrors the manual attach:

    CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...', ENDPOINT '127.0.0.1:9000',
                   URL_STYLE 'path', USE_SSL 0);
    ATTACH '<warehouse>' AS <alias> (TYPE ICEBERG, CLIENT_ID '...', CLIENT_SECRET '...',
                                     ENDPOINT 'http://127.0.0.1:8181');
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from catalogs.base import Catalog, CatalogConfig


class IcebergRestCatalog(Catalog):
    # Writable through DuckDB's Iceberg extension (REST catalog), so DM works.
    engine_writable = True
    # DuckDB has no Iceberg compaction step yet.
    supports_compaction = False

    def __init__(self, config: CatalogConfig):
        super().__init__(config)
        e = config.extra
        # REST catalog: ATTACH '<warehouse>' first-arg + endpoint/credentials.
        self.uri: str = e["uri"]
        self.warehouse: str = str(e.get("warehouse", ""))
        self.client_id: str | None = e.get("client_id")
        self.client_secret: str | None = e.get("client_secret")
        # S3-compatible storage (MinIO) secret.
        self.s3_endpoint: str | None = e.get("s3_endpoint")
        self.s3_access_key_id: str | None = e.get("s3_access_key_id")
        self.s3_secret_access_key: str | None = e.get("s3_secret_access_key")
        self.s3_url_style: str = str(e.get("s3_url_style", "path"))
        self.s3_use_ssl: bool = bool(e.get("s3_use_ssl", False))
        self.s3_region: str | None = e.get("s3_region")

    def provision(self, namespace: str, data_dir: Path, tables: list[str]) -> None:
        from setup.write_tables import write_tables

        # _write_via_duckdb attaches the catalog and CREATE SCHEMA IF NOT EXISTS + CTAS.
        write_tables(catalog=self, namespace=namespace, data_dir=data_dir, tables=tables)

    def teardown(self, namespace: str, tables: list[str]) -> None:
        import duckdb

        from engines.duckdb.catalog_adapters import attach_catalog

        with duckdb.connect() as conn:
            alias = attach_catalog(conn, self)
            for table in tables:
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

    def catalog_info(self) -> dict[str, str | None]:
        service = "iceberg-rest"
        return {
            "table_format": self.config.extra.get("table_format", "iceberg"),
            "catalog_service": service,
            "catalog_name": self.config.extra.get("catalog_name", service),
            "catalog_region": self.s3_region,
            "storage_service": self.config.extra.get("storage_service", "minio"),
            "storage_region": self.s3_region,
        }

    def connection_properties(self) -> dict[str, Any]:
        return {
            "type": "iceberg_rest",
            "uri": self.uri,
            "warehouse": self.warehouse,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "s3_endpoint": self.s3_endpoint,
            "s3_access_key_id": self.s3_access_key_id,
            "s3_secret_access_key": self.s3_secret_access_key,
            "s3_url_style": self.s3_url_style,
            "s3_use_ssl": self.s3_use_ssl,
            "s3_region": self.s3_region,
        }
