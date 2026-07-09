-- Delete-Fact template (Method 2, spec Clause 5.3.8). Applied once per (fact table,
-- date range): {table}/{date_sk} are filled per channel (see DELETE_FACTS in
-- benchmarks/data_maintenance.py) and the (date1, date2) window is bound as parameters,
-- one execution per row of the round's delete.parquet.
--
-- This per-range form replaces the earlier single DELETE that joined date_dim against the
-- whole dm_delete table — that plan degrades on Spark (broadcast/semijoin) and on DuckDB
-- at high scale factors. Applying each of the 3 ranges separately is the spec's canonical
-- form and yields the identical final state.
DELETE FROM {table}
WHERE {date_sk} IN (
    SELECT d_date_sk FROM date_dim WHERE d_date BETWEEN ? AND ?
);
