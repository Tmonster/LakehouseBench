-- Delete-Fact: web channel (spec Clause 5.3.8). Deletes the web sales fact and its
-- correlated returns fact for every date in the round's delete ranges. dm_delete holds the
-- (date1, date2) windows staged for this round; joining date_dim resolves them to the
-- surrogate date keys the facts are keyed on. The union of the ranges = the whole round.
DELETE FROM web_sales
WHERE ws_sold_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete WHERE d_date BETWEEN date1 AND date2
);

DELETE FROM web_returns
WHERE wr_returned_date_sk IN (
    SELECT d_date_sk FROM date_dim, dm_delete WHERE d_date BETWEEN date1 AND date2
);
