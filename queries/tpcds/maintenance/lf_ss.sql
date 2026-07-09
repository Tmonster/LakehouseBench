-- LF_SS: Load-Fact store_sales.
-- Adapted from the TPC-DS toolkit reference (tpcds-tools/tests/lf_ss_t.sql) to the
-- vendored source schema (tpcds_source.sql) and DuckDB dialect. Resolves surrogate
-- keys against the current version of each dimension (rec_end_date IS NULL) and
-- computes the fact measures from the staged purchase source rows.
CREATE OR REPLACE TEMPORARY VIEW ssv AS
SELECT d_date_sk                                                           ss_sold_date_sk,
       t_time_sk                                                           ss_sold_time_sk,
       i_item_sk                                                           ss_item_sk,
       c_customer_sk                                                       ss_customer_sk,
       c_current_cdemo_sk                                                  ss_cdemo_sk,
       c_current_hdemo_sk                                                  ss_hdemo_sk,
       c_current_addr_sk                                                   ss_addr_sk,
       s_store_sk                                                          ss_store_sk,
       p_promo_sk                                                          ss_promo_sk,
       purc_purchase_id                                                    ss_ticket_number,
       plin_quantity                                                       ss_quantity,
       i_wholesale_cost                                                    ss_wholesale_cost,
       i_current_price                                                     ss_list_price,
       plin_sale_price                                                     ss_sales_price,
       (i_current_price - plin_sale_price) * plin_quantity                 ss_ext_discount_amt,
       plin_sale_price * plin_quantity                                     ss_ext_sales_price,
       i_wholesale_cost * plin_quantity                                    ss_ext_wholesale_cost,
       i_current_price * plin_quantity                                     ss_ext_list_price,
       i_current_price * s_tax_percentage                                  ss_ext_tax,
       plin_coupon_amt                                                     ss_coupon_amt,
       (plin_sale_price * plin_quantity) - plin_coupon_amt                 ss_net_paid,
       ((plin_sale_price * plin_quantity) - plin_coupon_amt)
           * (1 + s_tax_percentage)                                        ss_net_paid_inc_tax,
       ((plin_sale_price * plin_quantity) - plin_coupon_amt)
           - (plin_quantity * i_wholesale_cost)                            ss_net_profit
FROM   s_purchase
       LEFT JOIN customer ON purc_customer_id = c_customer_id
       LEFT JOIN store    ON purc_store_id = s_store_id
       LEFT JOIN date_dim ON TRY_CAST(purc_purchase_date AS DATE) = d_date
       LEFT JOIN time_dim ON purc_purchase_time = t_time,
       s_purchase_lineitem
       LEFT JOIN promotion ON plin_promotion_id = p_promo_id
       LEFT JOIN item      ON plin_item_id = i_item_id
WHERE  purc_purchase_id = plin_purchase_id
  AND  i_rec_end_date IS NULL
  AND  s_rec_end_date IS NULL;

-- Rows whose item_id did not resolve to a current item are dropped (they would
-- become valid only after the corresponding dimension-maintenance step, which is
-- out of scope for the fact-only maintenance path).
INSERT INTO store_sales SELECT * FROM ssv WHERE ss_item_sk IS NOT NULL;
