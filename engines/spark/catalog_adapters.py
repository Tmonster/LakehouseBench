"""
Translates a Catalog's connection_properties() into PySpark config key/value pairs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from catalogs.base import Catalog

SPARK_VERSION = "4.0"
ICEBERG_VERSION = "1.10.1"

_PACKAGES = ",".join([
    "software.amazon.awssdk:bundle:2.29.38",
    "com.github.ben-manes.caffeine:caffeine:3.1.8",
    "org.apache.commons:commons-configuration2:2.11.0",
    "software.amazon.s3tables:s3-tables-catalog-for-iceberg:0.1.8",
    f"org.apache.iceberg:iceberg-spark-runtime-{SPARK_VERSION}_2.13:{ICEBERG_VERSION}",
])

# Iceberg REST catalog with an S3-compatible (MinIO) backend: the Spark runtime plus the
# AWS bundle that supplies S3FileIO. No S3 Tables catalog jar needed here.
_REST_PACKAGES = ",".join([
    f"org.apache.iceberg:iceberg-spark-runtime-{SPARK_VERSION}_2.13:{ICEBERG_VERSION}",
    f"org.apache.iceberg:iceberg-aws-bundle:{ICEBERG_VERSION}",
])

# AWS Glue catalog: Iceberg Spark runtime + AWS bundle (GlueCatalog and S3FileIO both live
# in the aws-bundle). Same jar set as the REST/S3-backed catalog.
_GLUE_PACKAGES = ",".join([
    f"org.apache.iceberg:iceberg-spark-runtime-{SPARK_VERSION}_2.13:{ICEBERG_VERSION}",
    f"org.apache.iceberg:iceberg-aws-bundle:{ICEBERG_VERSION}",
])


def _spark_memory_gb() -> int:
    """Return 80% of total system RAM in whole gigabytes (minimum 1)."""
    total_bytes = psutil.virtual_memory().total
    return max(1, int(total_bytes * 0.8 / (1024 ** 3)))


def spark_catalog_alias(catalog: "Catalog") -> str:
    props = catalog.connection_properties()
    match props["type"]:
        case "s3tables":
            return "s3tablesbucket"
        case "iceberg_rest":
            return "iceberg_catalog"
        case "glue":
            return "glue_catalog"
        case _:
            raise ValueError(f"No Spark adapter for catalog type: {props['type']!r}")


def spark_config(catalog: "Catalog") -> dict[str, str]:
    props = catalog.connection_properties()
    match props["type"]:
        case "s3tables":
            return _s3tables_config(props)
        case "iceberg_rest":
            return _iceberg_rest_config(props)
        case "glue":
            return _glue_config(props)
        case _:
            raise ValueError(f"No Spark adapter for catalog type: {props['type']!r}")


def _iceberg_rest_config(props: dict) -> dict[str, str]:
    alias = "iceberg_catalog"
    scheme = "https" if props.get("s3_use_ssl") else "http"
    cfg = {
        "spark.jars.packages": _REST_PACKAGES,
        "spark.sql.extensions": (
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
        ),
        f"spark.sql.catalog.{alias}": "org.apache.iceberg.spark.SparkCatalog",
        f"spark.sql.catalog.{alias}.type": "rest",
        f"spark.sql.catalog.{alias}.uri": props["uri"],
        # S3-compatible (MinIO) storage via Iceberg's S3FileIO.
        f"spark.sql.catalog.{alias}.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        f"spark.sql.catalog.{alias}.s3.endpoint": f"{scheme}://{props['s3_endpoint']}",
        f"spark.sql.catalog.{alias}.s3.path-style-access": "true",
        f"spark.sql.catalog.{alias}.s3.access-key-id": props["s3_access_key_id"],
        f"spark.sql.catalog.{alias}.s3.secret-access-key": props["s3_secret_access_key"],
        "spark.driver.memory": f"{_spark_memory_gb()}g",
        "spark.driver.maxResultSize": "1g",
        "spark.executor.memory": f"{_spark_memory_gb()}g",
        "spark.executor.memoryOverhead": "2g",
        "spark.local.dir": "./spark-spill",
        # Merge-on-read deletes so DELETE writes position-delete files (parity with the
        # DuckLake/DuckDB maintenance behavior, and what the compaction benchmark rewrites).
        f"spark.sql.catalog.{alias}.write.delete.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.update.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.merge.mode": "merge-on-read",
        "spark.scheduler.mode": "FAIR",
        "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
        "spark.kryoserializer.buffer.max": "128m",
    }
    if props.get("warehouse"):
        cfg[f"spark.sql.catalog.{alias}.warehouse"] = props["warehouse"]
    # OAuth2 client credentials, if the REST catalog requires them (client_id:client_secret).
    if props.get("client_id") and props.get("client_secret"):
        cfg[f"spark.sql.catalog.{alias}.credential"] = (
            f"{props['client_id']}:{props['client_secret']}"
        )
    if props.get("s3_region"):
        cfg[f"spark.sql.catalog.{alias}.client.region"] = props["s3_region"]
    return cfg


def _glue_config(props: dict) -> dict[str, str]:
    alias = "glue_catalog"
    cfg = {
        "spark.jars.packages": _GLUE_PACKAGES,
        "spark.sql.extensions": (
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
        ),
        f"spark.sql.catalog.{alias}": "org.apache.iceberg.spark.SparkCatalog",
        f"spark.sql.catalog.{alias}.catalog-impl": "org.apache.iceberg.aws.glue.GlueCatalog",
        # Data files live in S3 under base_location; read/write them via Iceberg's S3FileIO.
        f"spark.sql.catalog.{alias}.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        f"spark.sql.catalog.{alias}.warehouse": props["base_location"],
        f"spark.sql.catalog.{alias}.client.region": props["region"],
        "spark.driver.memory": f"{_spark_memory_gb()}g",
        "spark.driver.maxResultSize": "1g",
        "spark.executor.memory": f"{_spark_memory_gb()}g",
        "spark.executor.memoryOverhead": "2g",
        "spark.local.dir": "./spark-spill",
        # Merge-on-read deletes: DELETE writes position-delete files (parity with the other
        # catalogs and what the compaction benchmark rewrites).
        f"spark.sql.catalog.{alias}.write.delete.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.update.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.merge.mode": "merge-on-read",
        "spark.scheduler.mode": "FAIR",
        "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
        "spark.kryoserializer.buffer.max": "128m",
    }
    # Glue catalog id (the AWS account owning the catalog). Optional — omit to use the
    # caller's default account. AWS credentials come from the default provider chain
    # (instance profile / env), same as the DuckDB Glue adapter.
    if props.get("account_id"):
        cfg[f"spark.sql.catalog.{alias}.glue.id"] = props["account_id"]
    return cfg


def _s3tables_config(props: dict) -> dict[str, str]:
    alias = "s3tablesbucket"
    return {
        "spark.jars.packages": _PACKAGES,
        "spark.sql.extensions": (
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
        ),
        f"spark.sql.catalog.{alias}": "org.apache.iceberg.spark.SparkCatalog",
        f"spark.sql.catalog.{alias}.catalog-impl": (
            "software.amazon.s3tables.iceberg.S3TablesCatalog"
        ),
        f"spark.sql.catalog.{alias}.warehouse": props["s3tables_arn"],
        f"spark.sql.catalog.{alias}.client.region": props["region"],
        "spark.driver.memory": f"{_spark_memory_gb()}g",
        "spark.driver.maxResultSize": "1g",
        "spark.executor.memory": f"{_spark_memory_gb()}g",
        "spark.executor.memoryOverhead": "2g",
        "spark.local.dir": "./spark-spill",
        # Use merge-on-read for DELETE/UPDATE/MERGE so Spark writes position-delete
        # files rather than rewriting data files. This avoids ValidationException
        # ("Missing required files to delete") caused by S3 Tables background
        # optimization rewriting Parquet files between DELETE planning and commit.
        f"spark.sql.catalog.{alias}.write.delete.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.update.mode": "merge-on-read",
        f"spark.sql.catalog.{alias}.write.merge.mode": "merge-on-read",
        # Fair scheduler allows concurrent jobs from multiple threads to actually
        # run in parallel rather than being queued FIFO (needed for throughput test).
        "spark.scheduler.mode": "FAIR",
        "spark.serializer":"org.apache.spark.serializer.KryoSerializer",
        "spark.kryoserializer.buffer.max":"128m"
    }


