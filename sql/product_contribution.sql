-- 按商品编码统一汇总，避免同一商品因名称变化被拆开。
WITH products AS (
    SELECT stock_code, ROUND(SUM(line_value), 2) AS sales,
           SUM(quantity) AS quantity, COUNT(DISTINCT invoice) AS orders
    FROM transactions
    WHERE is_return = 0 AND is_service = 0
    GROUP BY stock_code
)
SELECT *, sales * 1.0 / NULLIF(SUM(sales) OVER (), 0) AS sales_share,
       SUM(sales) OVER (ORDER BY sales DESC, stock_code ROWS UNBOUNDED PRECEDING)
       * 1.0 / NULLIF(SUM(sales) OVER (), 0) AS cumulative_share
FROM products
ORDER BY sales DESC, stock_code;
