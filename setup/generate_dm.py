"""
Generate spec-faithful TPC-DS Data Maintenance (refresh) source data.

The DuckDB tpcds extension can only produce base tables, so refresh sets come from
the official toolkit's dsdgen with the `-update` flag (vendored as the tpcds-tools
submodule). Each update set `u` yields:

  * source-schema tables (s_purchase, s_catalog_order(+lineitem), s_web_order(+lineitem),
    s_store/catalog/web_returns, s_inventory, and dimension source updates) — the raw
    operational data the Load-Fact (LF_) maintenance functions consume.
  * delete_u.dat / inventory_delete_u.dat — (date1, date2) range pairs the Delete-Fact
    (DF_) functions use to remove sales/returns/inventory rows.

These are converted to Parquet under data/tpcds/sf=<N>/dm/round_<u>/ so the maintenance
benchmark (Phase 2) can stage and apply them. Column schemas are parsed from the
toolkit's tpcds_source.sql so we never hand-maintain them.

Usage:
    python -m setup.generate_dm --sf 1 --dm-sets 2
    python -m setup.generate_dm --sf 10 --dm-sets 3 --data-dir /tmp/tpcds
"""
from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

TOOLS_DIR = Path("tpcds-tools/tools")
DSDGEN_BIN = TOOLS_DIR / "dsdgen"
SOURCE_DDL = TOOLS_DIR / "tpcds_source.sql"

# Delete files carry (date1, date2) range pairs, not a create-table DDL.
DELETE_SCHEMA: dict[str, str] = {"date1": "DATE", "date2": "DATE"}
DELETE_FILES = ("delete", "inventory_delete")


# ---------------------------------------------------------------------------
# Source-schema parsing (tpcds_source.sql -> {table: {column: duckdb_type}})
# ---------------------------------------------------------------------------

def _ddl_type_to_duckdb(raw: str) -> str:
    raw = raw.strip().lower()
    if raw.startswith(("char", "varchar")):
        return "VARCHAR"
    if raw.startswith("smallint"):
        return "SMALLINT"
    if raw.startswith("bigint"):
        return "BIGINT"
    if raw.startswith("integer") or raw == "int":
        return "INTEGER"
    if raw.startswith(("decimal", "numeric")):
        return raw.upper()          # preserve DECIMAL(p,s)
    if raw.startswith("date"):
        return "DATE"
    if raw.startswith("time"):
        return "INTEGER"            # source *_time columns are integer seconds-of-day
    raise ValueError(f"Unhandled source DDL type: {raw!r}")


def parse_source_schemas(ddl_path: Path = SOURCE_DDL) -> dict[str, dict[str, str]]:
    """Parse `create table s_x (...)` blocks into {table: {column: duckdb_type}}."""
    text = ddl_path.read_text()
    schemas: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"create\s+table\s+(\w+)\s*\((.*?)\)\s*;", text, re.IGNORECASE | re.DOTALL):
        table = m.group(1).lower()
        cols: dict[str, str] = {}
        for line in m.group(2).splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            name, type_tok = parts[0], parts[1]
            cols[name] = _ddl_type_to_duckdb(type_tok)
        if cols:
            schemas[table] = cols
    return schemas


# ---------------------------------------------------------------------------
# Toolkit compilation
# ---------------------------------------------------------------------------

def _compile_dsdgen() -> None:
    if DSDGEN_BIN.exists():
        return

    if not (TOOLS_DIR / "Makefile.suite").exists():
        print(
            f"error: tpcds-tools not found at {TOOLS_DIR}.\n"
            "Initialise the submodule:\n  git submodule update --init tpcds-tools",
            file=sys.stderr,
        )
        sys.exit(1)

    # The official TPC-DS 4.0.0 kit (tpcds-tools) has no MACOS target, but it builds under
    # the LINUX target on macOS once clang's diagnostics are silenced. Use OS=LINUX on both
    # platforms and override LINUX_CFLAGS with warning-suppression flags per compiler; the
    # Makefile's own -DLINUX define is preserved.
    os_name = "LINUX"
    if platform.system() == "Darwin":
        # clang: the kit is pre-C99 K&R C; -Wno-everything silences the lot.
        cflags = "-g -O2 -I. -Wno-everything"
    else:
        # gcc: -std=gnu89 restores pre-C99 implicit-int/-decl semantics; -w drops warnings.
        cflags = "-g -O2 -I. -std=gnu89 -w"
    print(f"Compiling dsdgen in {TOOLS_DIR} (OS={os_name})...")
    shutil.copy(TOOLS_DIR / "Makefile.suite", TOOLS_DIR / "Makefile")
    # Only build dsdgen — dsqgen needs lex/yacc and we already source the 99 queries
    # from the DuckDB tpcds extension.
    subprocess.run(
        ["make", f"OS={os_name}", f"LINUX_CFLAGS={cflags}", "dsdgen"],
        cwd=TOOLS_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if not DSDGEN_BIN.exists():
        print("error: dsdgen compilation succeeded but binary not found.", file=sys.stderr)
        sys.exit(1)
    print("dsdgen compiled successfully.")


# ---------------------------------------------------------------------------
# .dat -> Parquet conversion
# ---------------------------------------------------------------------------

def _convert_dat_to_parquet(dat_file: Path, out: Path, schema: dict[str, str]) -> int:
    """
    Convert a pipe-delimited .dat file (trailing |) to Parquet using the given schema.
    Returns the row count. The trailing empty column produced by the delimiter is dropped.
    """
    all_cols = {**schema, "_trailing": "VARCHAR"}
    columns_sql = ", ".join(f"'{c}': '{t}'" for c, t in all_cols.items())
    select_cols = ", ".join(schema.keys())
    with duckdb.connect() as conn:
        # quote='' disables quote handling — TPC-DS text fields contain bare apostrophes
        # and quotes that must be read literally. null_padding=true tolerates dsdgen's
        # habit of omitting trailing columns for some source tables (e.g. s_promotion),
        # padding the missing values (and the trailing-| sentinel) with NULL.
        conn.execute(f"""
            COPY (
                SELECT {select_cols}
                FROM read_csv(
                    '{dat_file}', sep='|', header=false, quote='',
                    auto_detect=false, null_padding=true, strict_mode=false,
                    columns={{{columns_sql}}}
                )
            ) TO '{out}' (FORMAT PARQUET);
        """)
        return conn.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_dm_sets(scale_factor: int, data_dir: Path, n_sets: int) -> None:
    """
    Generate n_sets refresh update sets as Parquet under data_dir/dm/round_<u>/.

    Set 1 is the first maintenance round; sets 2..n are subsequent rounds. Each round
    directory contains one Parquet per source table plus delete.parquet and
    inventory_delete.parquet.
    """
    _compile_dsdgen()
    schemas = parse_source_schemas()
    dsdgen = DSDGEN_BIN.resolve()

    print(f"Generating TPC-DS DM sets (sf={scale_factor}, sets=1..{n_sets})...")
    for u in range(1, n_sets + 1):
        raw_dir = (data_dir / "dm" / f"_raw_{u}").resolve()
        raw_dir.mkdir(parents=True, exist_ok=True)
        round_dir = data_dir / "dm" / f"round_{u}"
        round_dir.mkdir(parents=True, exist_ok=True)

        # dsdgen resolves its distribution index (tpcds.idx) relative to cwd.
        subprocess.run(
            [str(dsdgen), "-scale", str(scale_factor), "-update", str(u),
             "-dir", str(raw_dir), "-force", "-quiet"],
            cwd=TOOLS_DIR,
            check=True,
        )

        # Source tables: files are named <table>_<u>.dat.
        for dat in sorted(raw_dir.glob(f"s_*_{u}.dat")):
            table = dat.name[: -len(f"_{u}.dat")]
            if table not in schemas:
                print(f"  warning: no schema for {table}, skipping", file=sys.stderr)
                continue
            out = round_dir / f"{table}.parquet"
            n = _convert_dat_to_parquet(dat, out, schemas[table])
            print(f"  round {u}: {table} -> {out.name} ({n:,} rows)")

        # Delete files: <name>_<u>.dat with (date1, date2) ranges.
        for name in DELETE_FILES:
            dat = raw_dir / f"{name}_{u}.dat"
            if not dat.exists():
                continue
            out = round_dir / f"{name}.parquet"
            n = _convert_dat_to_parquet(dat, out, DELETE_SCHEMA)
            print(f"  round {u}: {name} -> {out.name} ({n:,} ranges)")

        shutil.rmtree(raw_dir, ignore_errors=True)

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sf", type=int, default=1, help="TPC-DS scale factor")
    parser.add_argument("--dm-sets", type=int, default=1, help="Number of update sets to generate")
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="Base data directory (default: data/tpcds/sf=<sf>)",
    )
    args = parser.parse_args()
    data_dir = args.data_dir if args.data_dir is not None else Path("data/tpcds") / f"sf={args.sf}"
    generate_dm_sets(scale_factor=args.sf, data_dir=data_dir, n_sets=args.dm_sets)
