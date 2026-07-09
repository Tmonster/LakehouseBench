# Lakehouse Benchmarking

TPC-H benchmarks comparing query engines (DuckDB and Spark) and table formats
(Iceberg and DuckLake) across multiple catalog and storage backends.

## Supported engines and catalogs

| Engine | s3tables | Glue | Iceberg REST | DuckLake |
|--------|----------|------|--------------|----------|
| DuckDB | ✓ | ✓ | ✓ | ✓ |
| Spark  | ✓ | ✗ | ✓ | ✗ |

TPC-DS `maintenance` and `compaction` run on both engines: DuckDB compacts **DuckLake**
(in-process rewrite/merge), Spark compacts **Iceberg** (`rewrite_data_files`). DuckDB's
Iceberg extension has no compaction step, so DuckDB+Iceberg does maintenance but not
compaction. The maintenance SQL is shared across engines (portable `TEMPORARY VIEW` +
`INSERT`/`DELETE`).

`table_format` is `iceberg` for s3tables/Glue/Iceberg REST and `ducklake` for DuckLake. DuckLake
stores its Parquet data either locally (`config/ducklake_local.yml`) or on S3
(`config/ducklake_remote_data.yml`); its metadata catalog is always a local SQLite file.

Unlike S3 Tables, AWS Glue does not manage table storage, so each table is created with an
explicit `location` derived from a `base_location` prefix in the config (see below).

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Java 11+ (Spark only)
- AWS credentials configured (s3tables, Glue, and DuckLake with data on S3)

## Installation

```bash
# DuckDB only
uv sync

# DuckDB + Spark
uv sync --extra spark
```

## Machine setup (EC2)

When running on an EC2 instance with NVMe storage, mount the physical drive to avoid Spark spill competing with EBS I/O. The script below finds the largest unmounted NVMe device and mounts it, then clones the repo onto it.

```bash
mount_name=$(sudo lsblk | awk '
NR > 1 && $1 ~ /^nvme/ && $7 == "" {
    size = $4; unit = substr(size, length(size)); value = substr(size, 1, length(size)-1);
    if (unit == "G") { value *= 1024^3; }
    else if (unit == "T") { value *= 1024^4; }
    else if (unit == "M") { value *= 1024^2; }
    else if (unit == "K") { value *= 1024; }
    if (value > max) { max = value; largest = $1; }
}
END { if (largest) print largest; }')

sudo mkfs -t xfs /dev/$mount_name
sudo mkdir -p $HOME/benchmark_mount
sudo mount /dev/$mount_name $HOME/benchmark_mount
sudo chown -R ubuntu:ubuntu $HOME/benchmark_mount

git clone https://github.com/Tmonster/LakehouseBench.git $HOME/benchmark_mount/LakehouseBench
cd $HOME/benchmark_mount/LakehouseBench
```

This is also available as `./setup/mount.sh`.

## Configuration

### Catalog

Select a catalog with `--catalog-config`. The default is `config/s3tables_catalog.yml`;
`config/ducklake_local.yml` is the self-contained option for DuckDB & DuckLake (no AWS required).

`table_format` is recorded with the results (`logs.csv`) so runs are comparable by
format — `iceberg` for the Iceberg catalogs, `ducklake` for DuckLake.

Two optional keys are accepted by every catalog config:

- **`catalog_name`** — a user-facing label recorded with the results and used as the plot
  series key. Defaults to the catalog type (`aws-s3tables`, `aws-glue`, `ducklake`, …).
  Give two configs that hit the *same* catalog distinct `catalog_name`s (e.g. different
  `table_properties`) to compare them side by side in `plot_results.py`. This can be used
  to distinguish between two of the same catalogs with different table properties
- **`table_properties`** — a map of Iceberg table properties applied on `CREATE TABLE`
  (emitted as a `WITH (...)` clause). Works for any Iceberg catalog written through DuckDB
  (s3tables, Glue). Example: `write.target-file-size-bytes: 134217728`.

**AWS S3 Tables** (`config/s3tables_catalog.yml`):
```yaml
type: s3tables
table_format: iceberg
catalog_name: aws-s3tables                  # optional; plot label, defaults to the type
region: eu-central-1
account_id: "123456789012"
bucket: my-s3-tables-bucket
namespace: benchmarks
```

**AWS Glue (DuckDB only)** (`config/glue_catalog.yml`):
```yaml
type: glue
table_format: iceberg
catalog_name: aws-glue                     # optional; plot label, defaults to the type
region: eu-central-1
account_id: "123456789012"                 # Glue catalog id (the ATTACH target)
namespace: my_glue_database                # = Glue database name
base_location: s3://my-bucket/iceberg/     # each table -> <base_location>/<namespace>/<table>/
table_properties:                          # optional Iceberg table properties
  write.target-file-size-bytes: 134217728
```

DuckDB attaches Glue with `ENDPOINT_TYPE 'GLUE'`. Because Glue does not manage storage,
every table is created with an explicit `location` beneath `base_location` (Iceberg tables
cannot share a location). To compare table-property variants, copy this config, change
`catalog_name`, and adjust `table_properties` — see `config/glue_target_file_size_500mb.yml`.

**DuckLake — local data (DuckDB only)** (`config/ducklake_local.yml`):
```yaml
type: ducklake
table_format: ducklake
catalog_name: ducklake-local            # optional; plot label, defaults to the type
namespace: benchmarks
metadata_path: ducklake/tpch.ducklake
data_path: ducklake/files
```

**DuckLake — data on S3 (DuckDB only)** (`config/ducklake_remote_data.yml`):
```yaml
type: ducklake
table_format: ducklake
catalog_name: ducklake-remote           # optional; plot label, defaults to the type
namespace: benchmarks
metadata_path: ducklake/tpch.ducklake   # metadata stays local (SQLite)
data_path: s3://my-bucket/ducklake/      # Parquet data on S3 (AWS provider chain)
region: eu-central-1
```

**Iceberg REST (DuckDB, e.g. a local REST catalog + MinIO):**
```yaml
type: iceberg_rest
table_format: iceberg
catalog_name: iceberg-rest-local        # optional; plot label, defaults to the type
namespace: benchmarks
uri: http://127.0.0.1:8181              # Iceberg REST endpoint
warehouse: ""                           # first ATTACH arg (warehouse name/path)
client_id: admin
client_secret: password
s3_endpoint: 127.0.0.1:9000             # S3-compatible storage (MinIO)
s3_access_key_id: admin
s3_secret_access_key: password
s3_url_style: path
s3_use_ssl: false
```
Unlike the AWS catalogs, this runs fully locally and DuckDB can **write** to it, so it
supports the data-maintenance benchmark (compaction is still DuckLake-only).

### Benchmark (`config/benchmark.yml`)

```yaml
scale_factor: 10
warmup_runs: 1
benchmark_runs: 3
result_dir: results/
```

## Data generation

Generate TPC-H base tables for a given scale factor. Data is stored in `data/sf=<N>/`.
Base tables are generated with [`tpchgen-cli`](https://github.com/clflushopt/tpchgen-rs)
(a fast Rust generator, installed by `uv sync`); refresh sets and query streams use the
`dbgen`/`qgen` binaries from the submodule.

```bash
# Base tables only
uv run python -m setup.generate_data --sf 10

# Base tables + RF1/RF2 refresh files (required for power benchmark)
uv run python -m setup.generate_data --sf 10 --refresh

# Base tables + refresh files + spec-compliant per-stream query files
uv run python -m setup.generate_data --sf 10 --refresh --query-streams
```

`--query-streams` uses `qgen` to generate a different query permutation and parameter substitution for each stream, matching the TPC-H spec. Without it, the power benchmark falls back to a fixed query order and hardcoded parameters.

### TPC-DS data

Pass `--suite tpcds` to generate the 24 TPC-DS base tables (via DuckDB's `tpcds`
extension), the 99 query files, and SF-1 reference answers under `data/tpcds/sf=<N>/`
and `queries/tpcds/`. Add `--dm-sets N` to also generate `N` spec-faithful
data-maintenance update sets (`data/tpcds/sf=<N>/dm/round_<u>/`), which require the
`tpcds-tools` submodule (`git submodule update --init tpcds-tools`; `dsdgen` is compiled
on first use).

```bash
# TPC-DS base tables + queries + answers
uv run python -m setup.generate_data --suite tpcds --sf 10

# ... plus 8 data-maintenance update sets (for maintenance/compaction benchmarks)
uv run python -m setup.generate_data --suite tpcds --sf 10 --dm-sets 8
```

### Answers and verification

After the base tables are written, `generate_data` also generates the **expected query
answers** by querying the just-written Parquet with DuckDB, into
`queries/tpch/answers/sf<N>/`. The analytical benchmark compares each query's output
against these (recorded as `result_correct` in `time.csv`). Pass `--no-answers` to skip
this step, or regenerate answers separately:

```bash
uv run python -m setup.generate_answers --sf 10
```

Because answers are derived from the same data the benchmark queries, they stay
self-consistent on each machine — regenerate them wherever you generate the data
(generator versions can differ in the random text columns).

## Running benchmarks

Every run requires the `BENCH_INSTANCE_TYPE` environment variable — it is recorded
with the results (as `bench_instance_type`) so runs can be attributed to a machine.
The benchmark exits immediately if it is unset.

```bash
export BENCH_INSTANCE_TYPE=m5.4xlarge
```

DuckDB's external file cache is disabled for every run, so repeated queries always go
back to storage rather than serving hot data from cache — keeping comparisons fair
across catalogs and across the warmup/repeat runs.

### Load benchmark

Times how long it takes to provision the TPC-H tables into the catalog. This benchmark performs the data load itself — do not use `--skip-datagen`.

```bash
uv run python run_benchmark.py --engine duckdb --benchmark load --sf 10
```

### Analytical benchmark

> For S3Tables, a number of benchmark results can be skewed due to automatic compaction. For the analytical benchmark, run the Load benchmark first, and wait 10-20 minutes for automatic compaction to trigger. There is currently no way to disable compaction schema-wide in S3Tables

Runs the 22 TPC-H queries with configurable warmup and benchmark repetitions. No refresh functions.

```bash
# Provision data and run
uv run python run_benchmark.py --engine duckdb --benchmark analytical --sf 10

# Re-use an existing namespace (skip data generation)
uv run python run_benchmark.py --engine duckdb --benchmark analytical --sf 10 \
    --skip-datagen --namespace my_namespace --keep-tables
```

`--keep-tables` prevents the namespace from being dropped after the run, useful when you want to run multiple engines against the same data.

### Power benchmark

Runs the TPC-H power test: RF1 → single query stream → RF2 (sequential). Computes the power score. Requires refresh data — generate with `--refresh`.

```bash
uv run python run_benchmark.py --engine duckdb --benchmark power --sf 10
```

### Throughput benchmark

Runs N parallel query streams alongside a refresh thread. Computes the throughput score. The number of streams is determined by the TPC-H spec (e.g. 3 streams at SF=10). The number of refresh sets defaults to `max(1, round(0.1 * SF))`.

```bash
uv run python run_benchmark.py --engine duckdb --benchmark throughput --sf 10
```

### Composite benchmark

Runs the full TPC-H composite metric: power test followed by throughput test. Computes power score, throughput score, and the official QphH composite score (`sqrt(power_score * throughput_score)`).

```bash
uv run python run_benchmark.py --engine duckdb --benchmark composite --sf 10
```

With a non-default catalog config:
```bash
uv run python run_benchmark.py --engine duckdb --benchmark composite --sf 10 \
    --catalog-config config/ducklake_local.yml
```

### TPC-DS: analytical, data maintenance, and compaction

Pass `--suite tpcds` (DuckLake on DuckDB). The `--dm-rounds N` flag applies `N`
spec-faithful data-maintenance rounds; for `analytical`/`compaction` the rounds are
applied untimed to reach a degenerate state, and for `maintenance` the rounds
themselves are the timed unit.

```bash
# 99-query analytical benchmark (verified against SF-1 answers at 0 rounds)
uv run python run_benchmark.py --engine duckdb --suite tpcds --benchmark analytical --sf 10 \
    --catalog-config config/ducklake_local.yml

# Time the data-maintenance rounds themselves (per LF/DF function)
uv run python run_benchmark.py --engine duckdb --suite tpcds --benchmark maintenance --sf 10 \
    --dm-rounds 8 --catalog-config config/ducklake_local.yml

# Analytical latency after 8 rounds of maintenance (degenerate table, verification off)
uv run python run_benchmark.py --engine duckdb --suite tpcds --benchmark analytical --sf 10 \
    --dm-rounds 8 --catalog-config config/ducklake_local.yml

# Time compaction of a table degenerated by 8 maintenance rounds
uv run python run_benchmark.py --engine duckdb --suite tpcds --benchmark compaction --sf 10 \
    --dm-rounds 8 --catalog-config config/ducklake_compaction.yml

# Sweep: compaction at every DM depth 1..10 in one run_id (one time-vs-rounds curve).
# Each depth re-provisions a fresh degenerate table, so this is slow but self-contained.
uv run python run_benchmark.py --engine duckdb --suite tpcds --benchmark compaction --sf 10 \
    --dm-rounds-start 1 --dm-rounds-end 10 --catalog-config config/ducklake_compaction.yml
```

The `compaction` benchmark uses `config/ducklake_compaction.yml`, which sets
`data_inlining_row_limit: 0` so every commit writes a real data file (exposing the
small-file degeneration to measure, and matching Iceberg/Delta behavior). It records
the before/after data-file and delete-file counts alongside the compaction time. All
three sales channels (store/catalog/web) churn under maintenance; inventory and the
dimension SCD updates are not yet implemented.

With `--dm-rounds-start`/`--dm-rounds-end`, one benchmark run measures compaction at each
DM depth in the range and records them under a single `run_id`, so `plot_compaction.py`
draws the full time-vs-rounds curve from one run.

## Results

Every benchmark run appends to two append-only CSV files in `results/`. They
accumulate across runs (nothing is overwritten) and join on `run_id`:

- **`logs.csv`** — one row per run. Columns:

  | Column | Notes |
  |--------|-------|
  | `run_id` | Joins to `time.csv` |
  | `benchmark_start_time`, `benchmark_end_time` | Cover only the query phase — provisioning/data-load is excluded (except for the `load` benchmark, where the load *is* the workload) |
  | `bench_instance_type` | From the `BENCH_INSTANCE_TYPE` env var |
  | `benchmark` | `load` / `analytical` / `power` / `throughput` / `composite` |
  | `namespace` | |
  | `scale_factor` | |
  | `engine`, `engine_version` | e.g. `duckdb` / `1.5.2` |
  | `table_format` | `iceberg`, `ducklake` (`delta` eventually) |
  | `catalog_service` | The catalog kind: e.g. `aws-s3tables`, `aws-glue`, `ducklake`, `sqlite` |
  | `catalog_name` | The config's `catalog_name` label (defaults to `catalog_service`); the plot series key |
  | `catalog_region` | Region of the catalog service, or blank if not hosted/regional |
  | `storage_service` | Where the data lives: `s3`, `gcs`, `azure`, `local` |
  | `storage_region` | Region of the storage (set for S3), else blank |
  | `power_score`, `throughput_score`, `composite_score` | Headline scores; blank when not applicable to the benchmark |

- **`time.csv`** — one row per query (or refresh function) execution: `run_id`,
  `engine`, `engine_version`, `scale_factor`, `bench_instance_type`, `benchmark`,
  `namespace`, `query`, `run`, `query_start_time`, `query_end_time`,
  `result_correct`, `error`, `rows_returned`. For analytical runs each query
  appears once per repetition (`run` = 0..N-1). Join to `logs.csv` on `run_id`
  for the catalog/storage/format details.

CSV is written via DuckDB so quoting/escaping of free-text fields (e.g. `error`)
is handled correctly. Load it back for ad-hoc analysis with DuckDB or pandas:

```python
import duckdb
duckdb.sql("""
  SELECT l.engine, l.engine_version, t.query, median(
           epoch(t.query_end_time::TIMESTAMP) - epoch(t.query_start_time::TIMESTAMP))
  FROM 'results/logs.csv' l JOIN 'results/time.csv' t USING (run_id)
  WHERE l.benchmark = 'analytical'
  GROUP BY ALL
""").show()
```

### Plotting results

Two plotting scripts read `results/` (install the extras with `uv sync --extra plot`):
`plot_results.py` for **per-query latency** (analytical benchmark), and `plot_scores.py`
for the headline **scores** (power / throughput / composite).

#### Per-query latency (`plot_results.py`)

`plot_results.py` plots the **analytical** benchmark. It selects the most recent run
per `(engine, engine_version, catalog_name, table_format)` — so different engine versions,
catalogs, and table formats each show as separate series — and draws per-query latency
(median with min/max whiskers across the run's repetitions). Because the series key is
`catalog_name` (not `catalog_service`), two configs that hit the same remote catalog with
different `table_properties` plot side by side. Series are ordered by engine, then catalog,
then table format, then version. Plotting for the other benchmark types will get their own
scripts.

`--sf` and `--instance` are **required** (one scale factor + instance type per plot),
unless `--run-ids` is used. The other filters narrow the runs before selection:

| Flag | Effect |
|------|--------|
| `--sf` | Scale factor (required unless `--run-ids`) |
| `--instance` | `bench_instance_type` (required unless `--run-ids`) |
| `--engine` | One or more engines (e.g. `--engine duckdb spark`) |
| `--engine-version` | One or more versions (e.g. `--engine-version 1.5.2 1.5.3`) |
| `--catalog` | One or more `catalog_name`s (e.g. `--catalog aws-glue glue_target_file_size_bytes_500mb`) |
| `--table-format` | One table format (e.g. `iceberg`, `ducklake`) |
| `--storage` | `remote`, `local`, or a service (`s3`/`gcs`/`azure`) |
| `--benchmark` | Benchmark to plot (default: `analytical`) |
| `--run-ids` | Plot exactly these run_ids — bypasses the latest-per selection and all the filters above |
| `--results-dir` | Directory holding `logs.csv`/`time.csv` (default: `results`) |
| `--output` / `-o` | Save path (default: `results/images/tmp/<benchmark>.png`) |

Before plotting it prints the selected runs — each as `run_id  label  (started …)` — plus a
copy-pasteable `--run-ids …` line, so once your filters give the right set you can pin that
exact combination.

```bash
# iceberg vs ducklake at sf10, one machine, remote storage only
uv run --extra plot python plot_results.py --sf 10 --instance c8gd.4xlarge --storage remote

# compare two Glue table-property variants
uv run --extra plot python plot_results.py --sf 10 --instance c8gd.4xlarge \
    --catalog aws-glue glue_target_file_size_bytes_500mb

# just DuckDB, one format, two versions
uv run --extra plot python plot_results.py --sf 10 --instance c8gd.4xlarge \
    --engine duckdb --table-format iceberg --engine-version 1.5.3 1.5.4

# plot exactly these runs (ignores other filters; --sf/--instance not needed)
uv run --extra plot python plot_results.py --run-ids 1a2b3c4d 5e6f7a8b

uv run --extra plot python plot_results.py --sf 10 --instance c8gd.4xlarge -o analytical.png
```

#### Scores (`plot_scores.py`)

`plot_scores.py` plots the headline **scores** (`power_score`, `throughput_score`,
`composite_score`) straight from `logs.csv` — no `time.csv` needed. Bars are grouped by
score type, one bar per `(engine, engine_version, table_format)` series; higher is better
(QphH). For each metric it picks the most recent run that *has* that score, so a single
series may source `power` and `throughput` from different runs (e.g. a `power` run and a
`throughput` run). Series are ordered by engine, then table format, then version.

> Unlike `plot_results.py`, this script does not yet key on `catalog_name` and its filters
> are single-valued (and `--sf`/`--instance` are optional). Two configs hitting the same
> catalog with different `table_properties` will collapse to one series here.

| Flag | Effect |
|------|--------|
| `--metric` | One of `power` / `throughput` / `composite` (default: all three) |
| `--sf` | Filter to one scale factor |
| `--instance` | Filter to one `bench_instance_type` |
| `--engine` | Filter to one engine (e.g. `duckdb`) |
| `--table-format` | One table format (e.g. `iceberg`, `ducklake`) |
| `--storage` | `remote`, `local`, or a service (`s3`/`gcs`/`azure`) |
| `--run-ids` | Use exactly these run_ids — bypasses the latest-per selection and the filters above |
| `--results-dir` | Directory holding `logs.csv` (default: `results`) |
| `--output` / `-o` | Save path (default: `results/images/tmp/scores.png`) |

It prints each plotted score (`metric  label: score  (run_id, started …)`) before drawing.

```bash
# all three scores at sf10
uv run --extra plot python plot_scores.py --sf 10

# just the composite score, one engine
uv run --extra plot python plot_scores.py --sf 10 --engine duckdb --metric composite

# exactly these runs
uv run --extra plot python plot_scores.py --run-ids 1a2b3c4d 5e6f7a8b
```

#### Compaction (`plot_compaction.py`)

`plot_compaction.py` plots the headline compaction result: **compaction time vs
data-maintenance rounds**, one line per `(engine, catalog_name, scale_factor)` series. A
second panel shows the pre-compaction data-file count (`files_before`) for the same runs —
`dm_rounds` is only the knob, so the file count is the table's actual degeneration.

```bash
uv run --extra plot python plot_compaction.py --instance c8gd.4xlarge -o results/images/compaction.png
```

## Benchmark flags

| Flag | Description |
|------|-------------|
| `--engine` | `duckdb` or `spark` |
| `--benchmark` | `load`, `analytical`, `power`, `throughput`, or `composite` |
| `--sf` | Scale factor (overrides `benchmark.yml`) |
| `--catalog-config` | Path to catalog config (default: `config/s3tables_catalog.yml`) |
| `--benchmark-config` | Path to benchmark config (default: `config/benchmark.yml`) |
| `--namespace` | Namespace name (default: auto-generated) |
| `--keep-tables` | Skip namespace teardown after run |
| `--skip-datagen` | Skip data provisioning, use existing namespace (requires `--namespace`; not applicable to `load`) |
| `--update-streams` | Number of refresh sets in the throughput/composite test (default: `max(1, round(0.1 * SF))`) |

## Spark notes

- **Spill directory** — set to `./spark-spill` relative to the working directory. Change `spark.local.dir` in `engines/spark/catalog_adapters.py` if needed.
- **Driver/executor memory** — set to 25 GB. Adjust in `catalog_adapters.py` for your instance.
- **Delete strategy** — configured as merge-on-read. Copy-on-write causes `ValidationException: Missing required files to delete` on S3 Tables due to background file optimization rewriting Parquet files between DELETE planning and commit.
- **Scheduler** — `spark.scheduler.mode=FAIR` is required for the throughput test so parallel query streams actually run concurrently rather than queuing.


