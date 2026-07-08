-- DF_WS: Delete-Fact web_sales (and correlated web_returns) over the round's
-- (date1, date2) windows staged in dm_delete, resolved through date_dim.
DELETE FROM web_sales
WHERE ws_sold_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);

DELETE FROM web_returns
WHERE wr_returned_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete
    WHERE d_date BETWEEN date1 AND date2
);
