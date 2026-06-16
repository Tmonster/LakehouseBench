from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CatalogConfig:
    type: str
    namespace: str
    extra: dict[str, Any] = field(default_factory=dict)


class Catalog(ABC):
    def __init__(self, config: CatalogConfig):
        self.config = config

    @abstractmethod
    def provision(self, namespace: str, data_dir: Path) -> None:
        """Create namespace and write TPC-H Iceberg tables from data_dir Parquet files."""

    @abstractmethod
    def teardown(self, namespace: str) -> None:
        """Drop namespace and all tables within it."""

    @abstractmethod
    def table_ref(self, table: str, namespace: str | None = None) -> str:
        """Return a fully-qualified table reference string for use in engine queries."""

    @abstractmethod
    def connection_properties(self) -> dict[str, Any]:
        """Return engine-agnostic properties that catalog adapters translate per-engine."""

    def catalog_info(self) -> dict[str, str | None]:
        """
        Catalog + storage location metadata recorded with results (in logs.csv), so
        runs against the same catalog/storage are comparable.

        Keys:
          table_format     — table format of the data: iceberg, ducklake, delta
          catalog_service  — e.g. aws-s3tables, aws-glue, ducklake, polaris
          catalog_region   — region of the catalog service, or None if not hosted/regional
          storage_service  — where the data lives: s3, gcs, azure, local
          storage_region   — region of the storage (set for AWS/s3), else None
        """
        return {
            "table_format": None,
            "catalog_service": None,
            "catalog_region": None,
            "storage_service": None,
            "storage_region": None,
        }
