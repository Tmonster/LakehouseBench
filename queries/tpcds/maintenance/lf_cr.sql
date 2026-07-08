-- LF_CR: Load-Fact catalog_returns. Adapted from tpcds-tools/tests/lf_cr_t.sql.
-- Two reconciliations vs the reference: this kit's catalog_returns has NO cr_ship_date_sk
-- (the reference emits a 0 for it — dropped here), and the last credit column is named
-- cr_store_credit, not cr_merchant_credit. cr_catalog_page_sk / cr_ship_mode_sk /
-- cr_warehouse_sk are set to 0 as in the reference DM function.
CREATE OR REPLACE TEMP TABLE crv AS
SELECT d_date_sk                                                           cr_returned_date_sk,
       t_time_sk                                                           cr_returned_time_sk,
       i_item_sk                                                           cr_item_sk,
       c1.c_customer_sk                                                    cr_refunded_customer_sk,
       c1.c_current_cdemo_sk                                               cr_refunded_cdemo_sk,
       c1.c_current_hdemo_sk                                               cr_refunded_hdemo_sk,
       c1.c_current_addr_sk                                                cr_refunded_addr_sk,
       c2.c_customer_sk                                                    cr_returning_customer_sk,
       c2.c_current_cdemo_sk                                               cr_returning_cdemo_sk,
       c2.c_current_hdemo_sk                                               cr_returning_hdemo_sk,
       c2.c_current_addr_sk                                                cr_returning_addr_sk,
       cc_call_center_sk                                                   cr_call_center_sk,
       0                                                                   cr_catalog_page_sk,
       0                                                                   cr_ship_mode_sk,
       0                                                                   cr_warehouse_sk,
       r_reason_sk                                                         cr_reason_sk,
       cret_order_id                                                       cr_order_number,
       cret_return_qty                                                     cr_return_quantity,
       cret_return_amt                                                     cr_return_amount,
       cret_return_tax                                                     cr_return_tax,
       cret_return_amt + cret_return_tax                                   cr_return_amt_inc_tax,
       cret_return_fee                                                     cr_fee,
       cret_return_ship_cost                                              cr_return_ship_cost,
       cret_refunded_cash                                                 cr_refunded_cash,
       cret_reversed_charge                                               cr_reversed_charge,
       cret_merchant_credit                                               cr_store_credit,
       cret_return_amt + cret_return_tax + cret_return_fee
           - cret_refunded_cash - cret_reversed_charge - cret_merchant_credit cr_net_loss
FROM   s_catalog_returns
       LEFT JOIN date_dim ON TRY_CAST(cret_return_date AS DATE) = d_date
       LEFT JOIN time_dim ON (TRY_CAST(substr(cret_return_time, 1, 2) AS INTEGER) * 3600
                            + TRY_CAST(substr(cret_return_time, 4, 2) AS INTEGER) * 60
                            + TRY_CAST(substr(cret_return_time, 7, 2) AS INTEGER)) = t_time
       LEFT JOIN item        ON cret_item_id = i_item_id
       LEFT JOIN customer c1 ON cret_return_customer_id = c1.c_customer_id
       LEFT JOIN customer c2 ON cret_refund_customer_id = c2.c_customer_id
       LEFT JOIN reason      ON cret_reason_id = r_reason_id
       LEFT JOIN call_center ON cret_call_center_id = cc_call_center_id
WHERE  i_rec_end_date IS NULL
  AND  cc_rec_end_date IS NULL;

INSERT INTO catalog_returns SELECT * FROM crv WHERE cr_item_sk IS NOT NULL;
