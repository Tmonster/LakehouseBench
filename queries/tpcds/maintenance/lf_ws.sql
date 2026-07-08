-- LF_WS: Load-Fact web_sales. Structurally mirrors lf_cs (same order/ship/customer/
-- warehouse/promotion layout) with web_site supplying the tax rate. Two corrections vs
-- tpcds-tools/tests/lf_ws_t.sql: that reference swaps the ws_web_page_sk / ws_web_site_sk
-- columns and mislabels several net_* measures — here ws_web_page_sk = web_page and
-- ws_web_site_sk = web_site, and the measures follow the (correct) catalog formulas.
CREATE OR REPLACE TEMP TABLE wsv AS
SELECT d1.d_date_sk                                                        ws_sold_date_sk,
       t_time_sk                                                           ws_sold_time_sk,
       d2.d_date_sk                                                        ws_ship_date_sk,
       i_item_sk                                                           ws_item_sk,
       c1.c_customer_sk                                                    ws_bill_customer_sk,
       c1.c_current_cdemo_sk                                               ws_bill_cdemo_sk,
       c1.c_current_hdemo_sk                                               ws_bill_hdemo_sk,
       c1.c_current_addr_sk                                                ws_bill_addr_sk,
       c2.c_customer_sk                                                    ws_ship_customer_sk,
       c2.c_current_cdemo_sk                                               ws_ship_cdemo_sk,
       c2.c_current_hdemo_sk                                               ws_ship_hdemo_sk,
       c2.c_current_addr_sk                                                ws_ship_addr_sk,
       wp_web_page_sk                                                      ws_web_page_sk,
       web_site_sk                                                         ws_web_site_sk,
       sm_ship_mode_sk                                                     ws_ship_mode_sk,
       w_warehouse_sk                                                      ws_warehouse_sk,
       p_promo_sk                                                          ws_promo_sk,
       word_order_id                                                       ws_order_number,
       wlin_quantity                                                       ws_quantity,
       i_wholesale_cost                                                    ws_wholesale_cost,
       i_current_price                                                     ws_list_price,
       wlin_sales_price                                                    ws_sales_price,
       (i_current_price - wlin_sales_price) * wlin_quantity                ws_ext_discount_amt,
       wlin_sales_price * wlin_quantity                                    ws_ext_sales_price,
       i_wholesale_cost * wlin_quantity                                    ws_ext_wholesale_cost,
       i_current_price * wlin_quantity                                     ws_ext_list_price,
       i_current_price * web_tax_percentage                               ws_ext_tax,
       wlin_coupon_amt                                                     ws_coupon_amt,
       wlin_ship_cost * wlin_quantity                                      ws_ext_ship_cost,
       (wlin_sales_price * wlin_quantity) - wlin_coupon_amt                ws_net_paid,
       ((wlin_sales_price * wlin_quantity) - wlin_coupon_amt)
           * (1 + web_tax_percentage)                                      ws_net_paid_inc_tax,
       (wlin_sales_price * wlin_quantity) - wlin_coupon_amt
           + (wlin_ship_cost * wlin_quantity)                            ws_net_paid_inc_ship,
       (wlin_sales_price * wlin_quantity) - wlin_coupon_amt
           + (wlin_ship_cost * wlin_quantity)
           + i_current_price * web_tax_percentage                        ws_net_paid_inc_ship_tax,
       ((wlin_sales_price * wlin_quantity) - wlin_coupon_amt)
           - (wlin_quantity * i_wholesale_cost)                          ws_net_profit
FROM   s_web_order
       LEFT JOIN date_dim d1 ON TRY_CAST(word_order_date AS DATE) = d1.d_date
       LEFT JOIN time_dim    ON word_order_time = t_time
       LEFT JOIN customer c1 ON word_bill_customer_id = c1.c_customer_id
       LEFT JOIN customer c2 ON word_ship_customer_id = c2.c_customer_id
       LEFT JOIN web_site    ON word_web_site_id = web_site_id
       LEFT JOIN ship_mode   ON word_ship_mode_id = sm_ship_mode_id,
       s_web_order_lineitem
       LEFT JOIN date_dim d2 ON TRY_CAST(wlin_ship_date AS DATE) = d2.d_date
       LEFT JOIN item        ON wlin_item_id = i_item_id
       LEFT JOIN web_page    ON wlin_web_page_id = wp_web_page_id
       LEFT JOIN warehouse   ON wlin_warehouse_id = w_warehouse_id
       LEFT JOIN promotion   ON wlin_promotion_id = p_promo_id
WHERE  word_order_id = wlin_order_id
  AND  i_rec_end_date IS NULL
  AND  web_rec_end_date IS NULL
  AND  wp_rec_end_date IS NULL;

INSERT INTO web_sales SELECT * FROM wsv WHERE ws_item_sk IS NOT NULL;
