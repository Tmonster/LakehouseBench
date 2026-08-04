# Report August 4.

This Report builds off of the July 16 report. 

Changes:
> TPCH power tests have been updated. Results are with a new development version of DuckDB that has better parquet write support. These were updated since the async-io write integration with Iceberg was a bit faulty, and no that was fixed. The Report from July 16 lacked benchmarks on writing Iceberg tables using the dev version specifically for this reason. The Power benchmark was the only benchmark that would stress this since it required reads and writes.

Unless otherwise stated, tables were written with DuckDB-Iceberg @ v1.5.4 to a glue catalog that does not have automatic compaction. This is to avoid any drastic performance differences in the middle of a benchmark.

#### TL;DR Analysis

For analytical tests, v1.5.2 and v1.5.4 have similar performance, with main (v2.0.0) showing major performance improvements. The performance improvements between v1.5.4 and main may be exaggerated, due to Iceberg tables not being written optimally. See section on S3Tables providing compaction for results on this.
For Power tests, 1.5.2 and v1.5.4 are also similar in performance, with main (v2.0.0) showing very poor performance. This is due to the refactored parquet writer due to asyn-io improvements. There are changes required in parquet writer settings that are not used in DuckDB-Iceberg. The parquet files written for Iceberg tables have 2048 rows per row group, which is not optimial for reading.

#### TL;DR Conclusion
DuckDB-Iceberg needs to make changes to the `row_group_size_bytes` and `target_file_size_bytes` characteristics of the parquet files storing Iceberg table data. There is already a 512mb target file size, but only 2048 rows are written per row group. With more rows written per row group, DuckDB performance will drastically increase.


## TPCH Analytical benchmarks

### Scale factor 10

![tpch @sf10](/results/reports/August_4_2026/images/tpch_sf10_1.5.4_vs_main.png)

Plotted with
```
uv run --extra plot plot_results/plot_results.py --run-ids 081b8f3e40a54c70bdc64e884a89a27c 80d7753d895c4e5c90ca67166cdce93a 46ecec53de2a484285d1c9a00b0ab3e2
```

### Scale factor 100

Tpch @ sf100
![tpch @sf100](/results/reports/August_4_2026/images/tpch_sf100_1.5.4_vs_main.png)
Duckdb v1.5.4 vs DuckDB @ current main.


Plotted with 
```
uv run --extra plot plot_results/plot_results.py --run-ids b980168fda8b483ea5173f3edab09766 0082eb3540e7471f93d9fbf5dd00c649 25e0ba7953424d00a090ee365a72925b
```

## TPCDS benchmarks

### Scale factor 10

![tpcds @ sf10](/results/reports/August_4_2026/images/tpcds_sf10_1.5.4_vs_main.png)

Duckdb v1.5.4 vs DuckDB @ current main

Plotted with
```
uv run --extra plot plot_results/plot_results.py --run-ids 79b2c6113a094a7b8e6558d7431c642d 5072f8baf34f40cf9048c45cc02d5efe 088c4e2ade1f4670b1b9a2929306193c
```

### Scale factor 100

![tpcds @ sf100](/results/reports/August_4_2026/images/tpcds_sf100_v1.5.4_vs_main.png)
Plotted with 
```
uv run --extra plot plot_results/plot_results.py --run-ids 3f2e7d7ef2fb432f9e0f7a119dae9398 a481fd20de294eb3b0aba81f7800a07d d7605023fffe44468cef2cb6da16330e
```

### TPCH & TPCDS compared with Spark

#### TPCH @ sf 100
![](/results/reports/August_4_2026/images/tpch_sf100_compare_spark.png)

Generated with 
```
uv run --extra plot plot_results/plot_results.py --run-ids b980168fda8b483ea5173f3edab09766 0082eb3540e7471f93d9fbf5dd00c649 a323fc2ea5294c7e93034ff4da42aa98
```

#### TPCDS @ sf 100
![](/results/reports/August_4_2026/images/tpcds_sf100_main_vs_spark_vs_1.5.4.png)

Generated with 
```
uv run --extra plot plot_results/plot_results.py --run-ids 3f2e7d7ef2fb432f9e0f7a119dae9398 a481fd20de294eb3b0aba81f7800a07d 75b1d4d9cc994d65ae6c6ed6277e4c8b
```

## TPCH (POWER) Benchmarks

For Power tests, 1.5.2 and v1.5.4 are also similar in performance, with main (v2.0.0) showing very poor performance. This is because the parquet writer was refactored due to async-io improvements. The way to set options for desired row-group-size-bytes and file size has changed and DuckDB-Iceberg does not hook into this logic yet. In DuckDB-Iceberg main parquet files for Iceberg tables have 2048 rows per row group, which is detrimental to read performance. This will be fixed before v2.0.0 is released

### SF 10

![tpch power @ sf100](/results/reports/August_4_2026/images/power_sf10.png)

Generated with 
```
uv run --extra plot plot_results/plot_results.py --benchmark power --run-ids 6fa23bee5b7d4cba826229c5709e1c6a db4f4db91c5f4cf29faf2500b51d8706 bba99c0d1ce94dc6a6d0813305a2ce01
```
### SF 100

![tpch power @ sf100](/results/reports/August_4_2026/images/power_sf100.png)

Generated with 
```
uv run --extra plot plot_results/plot_results.py --benchmark power --run-ids e1e1b8900c774c25b3d2f48c715947bc 25b8add6709247d98633b87ab70aef77 a0b129a2517c4677a615942c960915c6
```

## TPCH Analytical benchmark with S3Tables providing compaction

To show that Async-IO does not provide the (sometimes) 10x performance improvements, we also benchmarked against s3tables which provides automatic compaction. Since the Iceberg tables will contain parquet files with fewer row groups and more rows per row group, the performance of the table read will improve. With the same engine but different parquet files, we can see how much of a performance improvement async-io delivers.

### TPCH SF100


If we compare v1.5.4 no compaction vs. v1.5.4 with compaction (s3tables) vs. async-io we see the following
![](/results/reports/August_4_2026/images/duckdb_glue_vs_s3_tables_tpch_sf100.png)

Which shows us DuckDB-Iceberg can improve when it comes to writing efficient Iceberg tables. 

```
uv run --extra plot plot_results/plot_results.py --run-ids bbf42c0644da42f0b80e38ddff24de05 b980168fda8b483ea5173f3edab09766 0082eb3540e7471f93d9fbf5dd00c649
```


Below is DuckDB-Iceberg main (with compaction) vs DuckDB-Iceberg main (no compaction)
![tpch @sf100](/results/reports/August_4_2026/images/tpch_sf100_main_vs_main_s3tables.png)

Plotted with 
```
uv run --extra plot plot_results/plot_results.py --benchmark analytical --run-ids c2a8ccaa83044acc8d2cee2a13a1027c 0082eb3540e7471f93d9fbf5dd00c649
```

This further emphasizes that DuckDB-Iceberg can make improvements when it comes to writing Iceberg tables.


### Load Benchmark

This measures how long it takes to upload files.

SF 10 tpch
![tpch load sf100](/results/reports/August_4_2026/images/load_tpch_sf100.png)

generated with 
```
uv run --extra plot plot_results/plot_results.py --benchmark load --run-ids aa87c4e5e66b4cfd811e081267f30ac0 8c53468854284cd3b91cff007182c239
```

SF 100 tpch
![tpch load sf100](/results/reports/August_4_2026/images/load_tpch_sf100.png)

generated with 
```
uv run --extra plot plot_results/plot_results.py --benchmark load --run-ids 66662520adf6444ba756333f90c15aca 40b4abb92e274109ae47b9c42ce62463
```




