-- DF_CS: Delete-Fact catalog_sales (and correlated catalog_returns) over the round's
-- (date1, date2) windows staged in dm_delete, resolved through date_dim.
DELETE FROM catalog_sales
WHERE cs_sold_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);

DELETE FROM catalog_returns
WHERE cr_returned_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);
