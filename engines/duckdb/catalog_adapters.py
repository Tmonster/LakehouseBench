"""
Translates a Catalog's connection_properties() into DuckDB ATTACH statements.
Each catalog type gets its own _attach_* function.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from catalogs.base import Catalog

CATALOG_ALIAS = "iceberg_catalog"


def attach_catalog(conn: duckdb.DuckDBPyConnection, catalog: "Catalog") -> str:
    """
    Attach the catalog to a DuckDB connection.
    Returns the catalog alias to use in subsequent USE / qualified references.
    """
    props = catalog.connection_properties()
    catalog_type = props["type"]

    if catalog_type == "ducklake":
        return _attach_ducklake(conn, props)

    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute("INSTALL aws; LOAD aws;")
    conn.execute("INSTALL httpfs; LOAD httpfs;")

    # turn off external file cache so results are not hot from cache
    conn.execute("pragma enable_external_file_cache=false")
    conn.execute("SET preserve_insertion_order=false;")
    # conn.execute("SET httpfs_connection_caching=true")

    match catalog_type:
        case "s3tables":
            return _attach_s3tables(conn, props)
        case "glue":
            return _attach_glue(conn, props)
        case "iceberg_rest":
            return _attach_iceberg_rest(conn, props)
        case _:
            raise ValueError(f"No DuckDB catalog adapter for type: {catalog_type!r}")


def _attach_iceberg_rest(conn: duckdb.DuckDBPyConnection, props: dict) -> str:
    """
    Attach a self-hosted Iceberg REST catalog with an S3-compatible (e.g. MinIO) backend.

    Emits an explicit S3 secret (KEY_ID/SECRET/ENDPOINT) rather than the AWS credential
    chain, then ATTACHes the REST catalog with CLIENT_ID/CLIENT_SECRET + ENDPOINT.
    """
    sep = ",\n    "
    if props.get("s3_endpoint"):
        parts = [
            "TYPE S3",
            f"KEY_ID '{props['s3_access_key_id']}'",
            f"SECRET '{props['s3_secret_access_key']}'",
            f"ENDPOINT '{props['s3_endpoint']}'",
            f"URL_STYLE '{props.get('s3_url_style', 'path')}'",
            f"USE_SSL {1 if props.get('s3_use_ssl') else 0}",
        ]
        if props.get("s3_region"):
            parts.append(f"REGION '{props['s3_region']}'")
        conn.execute(f"CREATE OR REPLACE SECRET iceberg_rest_s3 (\n    {sep.join(parts)}\n);")

    attach_opts = ["TYPE ICEBERG"]
    if props.get("client_id"):
        attach_opts.append(f"CLIENT_ID '{props['client_id']}'")
    if props.get("client_secret"):
        attach_opts.append(f"CLIENT_SECRET '{props['client_secret']}'")
    attach_opts.append(f"ENDPOINT '{props['uri']}'")
    warehouse = props.get("warehouse", "")
    conn.execute(
        f"ATTACH '{warehouse}' AS {CATALOG_ALIAS} (\n    {sep.join(attach_opts)}\n);"
    )
    return CATALOG_ALIAS


def _attach_s3tables(conn: duckdb.DuckDBPyConnection, props: dict) -> str:
    conn.execute("""
        CREATE SECRET IF NOT EXISTS aws_creds (
            TYPE S3,
            PROVIDER CREDENTIAL_CHAIN
        );
    """)
    # NOTE: DuckDB REST catalog ATTACH syntax may require version >= 1.2 with
    # the iceberg extension. Verify exact parameter names against your DuckDB version.
    conn.execute(f"""
        ATTACH '{props["s3tables_arn"]}' AS {CATALOG_ALIAS} (
            TYPE ICEBERG,
            ENDPOINT_TYPE S3_TABLES
        );
    """)
    return CATALOG_ALIAS


def _attach_glue(conn: duckdb.DuckDBPyConnection, props: dict) -> str:
    conn.execute("""
        CREATE SECRET IF NOT EXISTS aws_creds (
            TYPE S3,
            PROVIDER CREDENTIAL_CHAIN
        );
    """)
    # Glue does not manage storage: each CREATE TABLE supplies its own 'location'
    # (see GlueCatalog.table_create_options). purge_requested false leaves data in
    # place on drop, matching the documented Glue ATTACH usage.
    conn.execute(f"""
        ATTACH '{props["account_id"]}' AS {CATALOG_ALIAS} (
            TYPE ICEBERG,
            ENDPOINT_TYPE 'GLUE',
            PURGE_REQUESTED false
        );
    """)
    return CATALOG_ALIAS


_DUCKLAKE_ALIAS = "ducklake_catalog"


def _attach_ducklake(conn: duckdb.DuckDBPyConnection, props: dict) -> str:
    from catalogs.ducklake import is_remote_data_path

    conn.execute("INSTALL ducklake; LOAD ducklake;")
    # turn off external file cache so results are not hot from cache
    conn.execute("pragma enable_external_file_cache=false")
    metadata_path = props["metadata_path"]
    data_path = props["data_path"]

    # The metadata catalog is always a local file — make sure its directory exists
    # (on a fresh checkout the parent dir may not exist yet, and ATTACH won't create it).
    Path(metadata_path).parent.mkdir(parents=True, exist_ok=True)

    if is_remote_data_path(data_path):
        # Object-store data path (e.g. s3://): DuckDB needs httpfs + S3 credentials.
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("INSTALL aws; LOAD aws;")
        region_clause = f",\n            REGION '{props['region']}'" if props.get("region") else ""
        conn.execute(f"""
            CREATE SECRET IF NOT EXISTS ducklake_s3 (
                TYPE S3,
                PROVIDER CREDENTIAL_CHAIN{region_clause}
            );
        """)
    else:
        Path(data_path).mkdir(parents=True, exist_ok=True)

    conn.execute(
        f"ATTACH 'ducklake:{metadata_path}' AS {_DUCKLAKE_ALIAS} (DATA_PATH '{data_path}')"
    )
    # Optionally disable small-write inlining so every commit writes a real data file
    # (see DuckLakeCatalog.data_inlining_row_limit). Persisted as a global DuckLake option.
    limit = props.get("data_inlining_row_limit")
    if limit is not None:
        conn.execute(
            f"CALL ducklake_set_option('{_DUCKLAKE_ALIAS}', 'data_inlining_row_limit', '{limit}')"
        )
    return _DUCKLAKE_ALIAS
