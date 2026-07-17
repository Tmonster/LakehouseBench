export BENCH_INSTANCE_TYPE=c8gd.4xlarge
CFG=config/glue_target_file_size_500mb.yml

# Spark lifecycle (one persistent table)

NS=tpch_sf10_clean
CMD="uv run python run_benchmark.py --engine duckdb --suite tpch --sf 10 --catalog-config --skip-datagen --keep-tables $CFG"

# 1. queries on the pristine table (provisions it, keeps it)
$CMD --benchmark analytical --namespace $NS --keep-tables

# round 1
$CMD --benchmark maintenance  --dm-round-only 1 --namespace $NS --skip-datagen
# round 2
$CMD --benchmark maintenance  --dm-round-only 2 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     2 --namespace $NS --skip-datagen
# round 3
$CMD --benchmark maintenance  --dm-round-only 3 --namespace $NS --skip-datagen
# round 4
$CMD --benchmark maintenance  --dm-round-only 4 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     4 --namespace $NS --skip-datagen

# compaction on the depth-5 table
# DuckDB currently does not support compaction. 
# $CMD --benchmark compaction   --dm-rounds     5 --names


uv run python run_benchmark.py --engine duckdb --namespace tpch_sf10_clean --suite tpch --sf 10 --skip-datagen --keep-tables --catalog-config config/glue_target_file_size_500mb.yml --benchmark analytical