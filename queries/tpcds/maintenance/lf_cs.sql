-- LF_CS: Load-Fact catalog_sales. Adapted from tpcds-tools/tests/lf_cs_t.sql to the
-- vendored source schema + DuckDB dialect. TRY_CAST guards the source's occasional
-- degenerate order/ship dates (e.g. '-4713-11-2') that the reference blanks out by hand.
CREATE OR REPLACE TEMP TABLE csv AS
SELECT d1.d_date_sk                                                        cs_sold_date_sk,
       t_time_sk                                                           cs_sold_time_sk,
       d2.d_date_sk                                                        cs_ship_date_sk,
       c1.c_customer_sk                                                    cs_bill_customer_sk,
       c1.c_current_cdemo_sk                                               cs_bill_cdemo_sk,
       c1.c_current_hdemo_sk                                               cs_bill_hdemo_sk,
       c1.c_current_addr_sk                                                cs_bill_addr_sk,
       c2.c_customer_sk                                                    cs_ship_customer_sk,
       c2.c_current_cdemo_sk                                               cs_ship_cdemo_sk,
       c2.c_current_hdemo_sk                                               cs_ship_hdemo_sk,
       c2.c_current_addr_sk                                                cs_ship_addr_sk,
       cc_call_center_sk                                                   cs_call_center_sk,
       cp_catalog_page_sk                                                  cs_catalog_page_sk,
       sm_ship_mode_sk                                                     cs_ship_mode_sk,
       w_warehouse_sk                                                      cs_warehouse_sk,
       i_item_sk                                                           cs_item_sk,
       p_promo_sk                                                          cs_promo_sk,
       cord_order_id                                                       cs_order_number,
       clin_quantity                                                       cs_quantity,
       i_wholesale_cost                                                    cs_wholesale_cost,
       i_current_price                                                     cs_list_price,
       clin_sales_price                                                    cs_sales_price,
       (i_current_price - clin_sales_price) * clin_quantity                cs_ext_discount_amt,
       clin_sales_price * clin_quantity                                    cs_ext_sales_price,
       i_wholesale_cost * clin_quantity                                    cs_ext_wholesale_cost,
       i_current_price * clin_quantity                                     cs_ext_list_price,
       i_current_price * cc_tax_percentage                                 cs_ext_tax,
       clin_coupon_amt                                                     cs_coupon_amt,
       clin_ship_cost * clin_quantity                                      cs_ext_ship_cost,
       (clin_sales_price * clin_quantity) - clin_coupon_amt                cs_net_paid,
       ((clin_sales_price * clin_quantity) - clin_coupon_amt)
           * (1 + cc_tax_percentage)                                       cs_net_paid_inc_tax,
       (clin_sales_price * clin_quantity) - clin_coupon_amt
           + (clin_ship_cost * clin_quantity)                             cs_net_paid_inc_ship,
       (clin_sales_price * clin_quantity) - clin_coupon_amt
           + (clin_ship_cost * clin_quantity)
           + i_current_price * cc_tax_percentage                          cs_net_paid_inc_ship_tax,
       ((clin_sales_price * clin_quantity) - clin_coupon_amt)
           - (clin_quantity * i_wholesale_cost)                           cs_net_profit
FROM   s_catalog_order
       LEFT JOIN date_dim d1  ON TRY_CAST(cord_order_date AS DATE) = d1.d_date
       LEFT JOIN time_dim     ON cord_order_time = t_time
       LEFT JOIN customer c1  ON cord_bill_customer_id = c1.c_customer_id
       LEFT JOIN customer c2  ON cord_ship_customer_id = c2.c_customer_id
       LEFT JOIN call_center  ON cord_call_center_id = cc_call_center_id
       LEFT JOIN ship_mode    ON cord_ship_mode_id = sm_ship_mode_id,
       s_catalog_order_lineitem
       LEFT JOIN date_dim d2     ON TRY_CAST(clin_ship_date AS DATE) = d2.d_date
       LEFT JOIN catalog_page    ON clin_catalog_page_number = cp_catalog_page_number
                                AND clin_catalog_number = cp_catalog_number
       LEFT JOIN warehouse       ON clin_warehouse_id = w_warehouse_id
       LEFT JOIN item            ON clin_item_id = i_item_id
       LEFT JOIN promotion       ON clin_promotion_id = p_promo_id
WHERE  cord_order_id = clin_order_id
  AND  i_rec_end_date IS NULL
  AND  cc_rec_end_date IS NULL;

INSERT INTO catalog_sales SELECT * FROM csv WHERE cs_item_sk IS NOT NULL;
