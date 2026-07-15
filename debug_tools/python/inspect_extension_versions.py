import duckdb
duckdb.__version__
con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
con.execute("SET custom_extension_repository = '/Users/tomebergen/git/LakehouseBench/.duckdb-python/external/duckdb/build/release/repository'")
con.execute("install avro; load avro")

print(con.execute("from duckdb_extensions() select extension_name, install_path, installed_from, extension_version where extension_name in ('iceberg', 'avro', 'httpfs');").fetchall())
# [('avro', '/Users/tomebergen/.duckdb/extensions/da3d58bbb1/osx_arm64/avro.duckdb_extension', '/Users/tomebergen/git/duckdb-python/external/duckdb/build/release/repository', 'd9dccda'), ('httpfs', '/Users/tomebergen/.duckdb/extensions/da3d58bbb1/osx_arm64/httpfs.duckdb_extension', '/Users/tomebergen/git/duckdb-python/external/duckdb/build/release/repository', '53c5b03'), ('iceberg', '/Users/tomebergen/.duckdb/extensions/da3d58bbb1/osx_arm64/iceberg.duckdb_extension', '/Users/tomebergen/git/duckdb-python/external/duckdb/build/release/repository', '6b949bac')]


print(con.execute("select version()").fetchall())


con.execute("CREATE SECRET (TYPE S3,KEY_ID 'admin',SECRET 'password',ENDPOINT '127.0.0.1:9000',URL_STYLE 'path',USE_SSL 0); ATTACH '' AS my_datalake (TYPE ICEBERG,CLIENT_ID 'admin',CLIENT_SECRET 'password',ENDPOINT 'http://127.0.0.1:8181'); Create schema if not exists my_datalake.default;")


# query 1
con.execute(".timer on")
con.execute("use my_datalake.test_tpch_async_sf1;")
con.execute("pragma enable_external_file_cache=false;")


def execute_q1(con):
	import time
	start = time.time()
	res = con.execute("""select
		l_returnflag,
		l_linestatus,
		sum(l_quantity) as sum_qty,
		sum(l_extendedprice) as sum_base_price,
		sum(l_extendedprice * (1 - l_discount)) as sum_disc_price,
		sum(l_extendedprice * (1 - l_discount) * (1 + l_tax)) as sum_charge,
		avg(l_quantity) as avg_qty,
		avg(l_extendedprice) as avg_price,
		avg(l_discount) as avg_disc,
		count(*) as count_order
		from
		lineitem
		where
		l_shipdate <= date '1998-12-01' - interval '90' day
		group by
		l_returnflag,
		l_linestatus
		order by
		l_returnflag,
		l_linestatus;"""
	  )
	res.fetchall()
	end = time.time()
	print(f"Q1 execution time is {end - start}")
