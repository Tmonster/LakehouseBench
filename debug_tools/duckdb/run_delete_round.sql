-- first create dm_delete table for the round
CREATE OR REPLACE TEMP TABLE dm_delete AS 
SELECT * FROM read_parquet('/path/to/tpcds/data/sf=1/dm/round_x/delete.parquet', hive_partitioning=false)

DELETE FROM store_sales 
WHERE ss_sold_date_sk IN (
	SELECT d_date_sk 
	FROM date_dim, dm_delete 
	WHERE d_date BETWEEN date1 AND date2
)
