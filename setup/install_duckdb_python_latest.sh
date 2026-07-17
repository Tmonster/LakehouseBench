#!/usr/bin/env bash
# Clone/build Tmonster/duckdb-python into a dir inside LakehouseBench and install
# it into LakehouseBench's venv, with loadable extensions built from the submodule.
# Run from anywhere inside the LakehouseBench working tree.
set -euo pipefail

LAKEHOUSE_ROOT="$(git rev-parse --show-toplevel)"

FORK_URL="${FORK_URL:-https://github.com/Tmonster/duckdb-python.git}"
UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/duckdb/duckdb-python.git}"
BRANCH="${BRANCH:-main}"
CLONE_DIR="${CLONE_DIR:-$LAKEHOUSE_ROOT/.duckdb-python}"
VENV_PY="${VENV_PY:-$LAKEHOUSE_ROOT/.venv/bin/python}"

# vcpkg is required to build aws/iceberg/avro; bail early if its toolchain is unset.
if [ -z "${VCPKG_TOOLCHAIN_PATH:-}" ]; then
  echo "!! VCPKG_TOOLCHAIN_PATH is not set — required to build the extensions. Exiting."
  exit 1
fi

[ -x "$VENV_PY" ] || { echo "!! venv python not found: $VENV_PY"; exit 1; }

if command -v sccache >/dev/null 2>&1; then
  export CMAKE_C_COMPILER_LAUNCHER="$(command -v sccache)"
  export CMAKE_CXX_COMPILER_LAUNCHER="$(command -v sccache)"
fi

# --- clone if missing, else update -----------------------------------------
if [ ! -d "$CLONE_DIR/.git" ]; then
  echo ">> cloning $FORK_URL ($BRANCH) into $CLONE_DIR"
  git clone --branch "$BRANCH" "$FORK_URL" "$CLONE_DIR"
  cd "$CLONE_DIR"
  # The fork has NO tags; the version scheme needs a reachable v*.*.0 tag or it
  # falls back to 0.0.1.dev1 and the build backend crashes. Pull tags from upstream.
  git remote add upstream "$UPSTREAM_URL"
  git fetch --tags --quiet upstream
else
  echo ">> reusing existing clone at $CLONE_DIR (updating $BRANCH)"
  cd "$CLONE_DIR"
  git fetch --quiet origin "$BRANCH"
  git checkout --quiet "$BRANCH"
  git pull --quiet --ff-only origin "$BRANCH" || echo "   (fast-forward pull skipped)"
  git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
  git fetch --tags --quiet upstream
fi

echo ">> version describe: $(git describe --tags --long --abbrev=8 --match 'v*.*.0' 2>&1 || echo '<none>')"
git submodule update --init --recursive
ENGINE="$(git -C external/duckdb rev-parse --short=10 HEAD)"
echo ">> engine commit: $ENGINE"

# --- build loadable extensions from the submodule --------------------------
# Built against the exact engine commit, so they load without an ABI mismatch.
# They are UNSIGNED, so runtime still needs allow_unsigned_extensions.
echo ">> building extensions (httpfs;avro;parquet;aws;iceberg) ..."
( cd external/duckdb && \
  USE_MERGED_VCPKG_MANIFEST=1 VCPKG_TOOLCHAIN_PATH="$VCPKG_TOOLCHAIN_PATH" \
  BUILD_EXTENSIONS="httpfs;avro;parquet;aws;iceberg" GEN=ninja make )

EXT_REPO="$CLONE_DIR/external/duckdb/build/release/repository"
echo ">> extension repository: $EXT_REPO"
ls -1 "$EXT_REPO/$ENGINE/"*/ 2>/dev/null || echo "   (check the repository path; layout may differ)"

# --- install the Python package into LakehouseBench's venv -----------------
# No OVERRIDE_GIT_DESCRIBE: tags are present, so source_id stays real and matches
# the extensions we just built.
unset OVERRIDE_GIT_DESCRIBE SETUPTOOLS_SCM_PRETEND_VERSION SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DUCKD
uv pip install --python "$VENV_PY" --force-reinstall "$CLONE_DIR"

# --- verify: version, source_id, and that iceberg actually loads -----------
EXT_REPO="$EXT_REPO" "$VENV_PY" - <<'PY'
import os, sys, duckdb
c = duckdb.connect()
src = c.sql("select source_id from pragma_version()").fetchone()[0]
print(f">> installed : {duckdb.__version__}  ({c.sql('select version()').fetchone()[0]})")
print(f">> source_id : {src}")
res = c.execute("from duckdb_extensions() select extension_name, install_path, installed_from, extension_version where extension_name in ('iceberg', 'avro', 'httpfs');")
ares = res.fetchall()
print(ares)
PY

cat <<EOF

>> done. To run the benchmark against these local extensions:

   export DUCKDB_EXTENSION_REPO="$EXT_REPO"

   and ensure every duckdb.connect() passes config={"allow_unsigned_extensions": True}.
EOF