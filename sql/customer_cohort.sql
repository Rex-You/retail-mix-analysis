-- 首购指数据观察窗口内首次交易，不能推断真实注册或首次购买日期。
WITH sales AS (
    SELECT customer_id, substr(invoice_date, 1, 7) AS order_month
    FROM transactions
    WHERE is_return = 0 AND customer_id <> ''
), first_orders AS (
    SELECT customer_id, MIN(order_month) AS cohort FROM sales GROUP BY customer_id
), active AS (
    SELECT f.cohort,
           (CAST(substr(s.order_month, 1, 4) AS INTEGER) - CAST(substr(f.cohort, 1, 4) AS INTEGER)) * 12
           + CAST(substr(s.order_month, 6, 2) AS INTEGER) - CAST(substr(f.cohort, 6, 2) AS INTEGER) AS age,
           COUNT(DISTINCT s.customer_id) AS active_customers
    FROM sales s JOIN first_orders f ON s.customer_id = f.customer_id
    GROUP BY f.cohort, age
), sizes AS (
    SELECT cohort, COUNT(*) AS cohort_size FROM first_orders GROUP BY cohort
)
SELECT a.*, z.cohort_size,
       a.active_customers * 1.0 / z.cohort_size AS retention
FROM active a JOIN sizes z USING (cohort)
ORDER BY a.cohort, a.age;
