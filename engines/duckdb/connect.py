"""
Shared DuckDB connection factory.

Normally a thin pass-through to duckdb.connect(). When a locally-built (unsigned) iceberg
extension is in play — signalled by DUCKDB_ICEBERG_EXTENSION or DUCKDB_EXTENSION_REPO — it
injects allow_unsigned_extensions into the startup config so LOAD accepts the unsigned
binary. That option is a startup-only setting (it cannot be SET on an open connection), so
it must be passed at connect() time; routing every duckdb.connect() in the repo through
this helper is what makes the local-build workflow work without patching each call site.

With neither env var set, behavior is identical to a bare duckdb.connect().
See engines/duckdb/catalog_adapters.py (_load_iceberg_extension) for the matching load path.
"""
from __future__ import annotations

import os

import duckdb


def local_extension_active() -> bool:
    """True when a local/custom DuckDB extension source is configured via env vars."""
    return bool(
        os.environ.get("DUCKDB_ICEBERG_EXTENSION")
        or os.environ.get("DUCKDB_EXTENSION_REPO")
    )


def connect(database: str = ":memory:", *, config: dict | None = None, **kwargs) -> duckdb.DuckDBPyConnection:
    """
    duckdb.connect() wrapper that enables unsigned extensions when a local build is active.

    Passes through database/config/kwargs unchanged otherwise. When no config is needed
    (the common released-DuckDB path), calls duckdb.connect() without a config arg so the
    behavior is byte-for-byte identical to before.
    """
    cfg = dict(config) if config else {}
    if local_extension_active():
        cfg.setdefault("allow_unsigned_extensions", True)
    if cfg:
        return duckdb.connect(database, config=cfg, **kwargs)
    return duckdb.connect(database, **kwargs)
