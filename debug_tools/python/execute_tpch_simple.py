import duckdb
import time
duckdb.__version__
con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
con.execute("SET custom_extension_repository = '/home/ubuntu/benchmark_mount/LakehouseBench/.duckdb-python/external/duckdb/build/release/repository'")
con.execute(f"SET autoinstall_extension_repository = '/home/ubuntu/benchmark_mount/LakehouseBench/.duckdb-python/external/duckdb/build/release/repository'")
con.execute("install avro; load avro")
con.execute("install httpfs; load httpfs")
con.execute("install iceberg; load iceberg")
con.execute("install aws; load aws")
# con.execute("CREATE SECRET (TYPE S3,KEY_ID 'admin',SECRET 'password',ENDPOINT '127.0.0.1:9000',URL_STYLE 'path',USE_SSL 0); ATTACH '' AS my_datalake (TYPE ICEBERG,CLIENT_ID 'admin',CLIENT_SECRET 'password',ENDPOINT 'http://127.0.0.1:8181'); Create schema if not exists my_datalake.default;")
con.execute("create secret blah (type s3, provider 'credential_chain', region 'eu-central-1');")
con.execute("attach '840140254803' as my_datalake (type iceberg, endpoint_type glue);")
con.execute("use my_datalake.bench_sf10")
con.execute("set enable_external_file_cache=false")



queries = [
"queries/tpch/queries/q01.sql",
"queries/tpch/queries/q02.sql",
"queries/tpch/queries/q03.sql",
# "queries/tpch/queries/q04.sql",
# "queries/tpch/queries/q05.sql",
# "queries/tpch/queries/q06.sql",
# "queries/tpch/queries/q07.sql",
# "queries/tpch/queries/q08.sql",
# "queries/tpch/queries/q09.sql",
# "queries/tpch/queries/q10.sql",
# "queries/tpch/queries/q11.sql",
# "queries/tpch/queries/q12.sql",
# "queries/tpch/queries/q13.sql",
# "queries/tpch/queries/q14.sql",
# "queries/tpch/queries/q15.sql",
# "queries/tpch/queries/q16.sql",
# "queries/tpch/queries/q17.sql",
# "queries/tpch/queries/q18.sql",
# "queries/tpch/queries/q19.sql",
# "queries/tpch/queries/q20.sql",
# "queries/tpch/queries/q21.sql",
# "queries/tpch/queries/q22.sql",
]

for q in queries:
	sql = open(q).read().rstrip().rstrip(";")
	print(f"Query {q}")
	for label, fn in [
		("execute only",  lambda: con.execute(sql)),
		("+ fetchall",    lambda: con.execute(sql).fetchall()),
		("+ arrow",       lambda: con.execute(sql).fetch_arrow_table()),
	]:
		t = time.perf_counter()
		_ = fn()
		print(f"{label:14} {time.perf_counter()-t:.3f}s")

