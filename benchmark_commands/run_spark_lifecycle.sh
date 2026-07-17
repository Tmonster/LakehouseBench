export BENCH_INSTANCE_TYPE=c8gd.4xlarge
CFG=config/glue_target_file_size_500mb.yml

# Spark lifecycle (one persistent table)

NS=lifecycle_spark_sf1
CMD="uv run --extra spark python run_benchmark.py --engine spark --suite tpcds --sf 10 --catalog-config $CFG"

# 1. queries on the pristine table (provisions it, keeps it)
$CMD --benchmark analytical --namespace $NS --keep-tables

# round 1
$CMD --benchmark maintenance  --dm-round-only 1 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     1 --namespace $NS --skip-datagen
# round 2
$CMD --benchmark maintenance  --dm-round-only 2 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     2 --namespace $NS --skip-datagen
# round 3
$CMD --benchmark maintenance  --dm-round-only 3 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     3 --namespace $NS --skip-datagen
# round 4
$CMD --benchmark maintenance  --dm-round-only 4 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     4 --namespace $NS --skip-datagen
# round 5
$CMD --benchmark maintenance  --dm-round-only 5 --namespace $NS --skip-datagen
$CMD --benchmark analytical   --dm-rounds     5 --namespace $NS --skip-datagen

# compaction on the depth-5 table
$CMD --benchmark compaction   --dm-rounds     5 --namespace $NS