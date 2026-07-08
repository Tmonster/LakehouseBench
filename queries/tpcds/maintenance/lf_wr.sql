-- LF_WR: Load-Fact web_returns. Adapted from tpcds-tools/tests/lf_wr_t.sql; this kit's
-- web_returns schema lines up 1:1 with the reference view (24 columns), so only the
-- date/time casts and the delete-free source column names needed adjusting.
CREATE OR REPLACE TEMP TABLE wrv AS
SELECT d_date_sk                                                           wr_returned_date_sk,
       t_time_sk                                                           wr_returned_time_sk,
       i_item_sk                                                           wr_item_sk,
       c1.c_customer_sk                                                    wr_refunded_customer_sk,
       c1.c_current_cdemo_sk                                               wr_refunded_cdemo_sk,
       c1.c_current_hdemo_sk                                               wr_refunded_hdemo_sk,
       c1.c_current_addr_sk                                                wr_refunded_addr_sk,
       c2.c_customer_sk                                                    wr_returning_customer_sk,
       c2.c_current_cdemo_sk                                               wr_returning_cdemo_sk,
       c2.c_current_hdemo_sk                                               wr_returning_hdemo_sk,
       c2.c_current_addr_sk                                                wr_returning_addr_sk,
       wp_web_page_sk                                                      wr_web_page_sk,
       r_reason_sk                                                         wr_reason_sk,
       wret_order_id                                                       wr_order_number,
       wret_return_qty                                                     wr_return_quantity,
       wret_return_amt                                                     wr_return_amt,
       wret_return_tax                                                     wr_return_tax,
       wret_return_amt + wret_return_tax                                   wr_return_amt_inc_tax,
       wret_return_fee                                                     wr_fee,
       wret_return_ship_cost                                              wr_return_ship_cost,
       wret_refunded_cash                                                 wr_refunded_cash,
       wret_reversed_charge                                               wr_reversed_charge,
       wret_account_credit                                                wr_account_credit,
       wret_return_amt + wret_return_tax + wret_return_fee
           - wret_refunded_cash - wret_reversed_charge - wret_account_credit wr_net_loss
FROM   s_web_returns
       LEFT JOIN date_dim ON TRY_CAST(wret_return_date AS DATE) = d_date
       LEFT JOIN time_dim ON (TRY_CAST(substr(wret_return_time, 1, 2) AS INTEGER) * 3600
                            + TRY_CAST(substr(wret_return_time, 4, 2) AS INTEGER) * 60
                            + TRY_CAST(substr(wret_return_time, 7, 2) AS INTEGER)) = t_time
       LEFT JOIN item        ON wret_item_id = i_item_id
       LEFT JOIN customer c1 ON wret_return_customer_id = c1.c_customer_id
       LEFT JOIN customer c2 ON wret_refund_customer_id = c2.c_customer_id
       LEFT JOIN reason      ON wret_reason_id = r_reason_id
       LEFT JOIN web_page    ON wret_web_page_id = wp_web_page_id
WHERE  i_rec_end_date IS NULL
  AND  wp_rec_end_date IS NULL;

INSERT INTO web_returns SELECT * FROM wrv WHERE wr_item_sk IS NOT NULL;
