-- DF_SS: Delete-Fact store_sales (and correlated store_returns).
-- Removes all store rows whose sold/returned date falls within any of the
-- (date1, date2) windows staged in dm_delete (from the round's delete.parquet).
-- The date ranges are resolved to surrogate keys through date_dim, matching the
-- fact tables' *_date_sk columns.
DELETE FROM store_sales
WHERE ss_sold_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);

DELETE FROM store_returns
WHERE sr_returned_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);
