SELECT
    date(substr(invoice_date, 1, 7) || '-01') AS month,
    MIN(date(invoice_date)) AS first_transaction,
    MAX(date(invoice_date)) AS last_transaction,
    SUM(CASE WHEN is_return = 0 THEN line_value ELSE 0 END) AS gross_sales,
    SUM(CASE WHEN is_return = 1 THEN -line_value ELSE 0 END) AS return_value,
    SUM(line_value) AS net_sales,
    COUNT(DISTINCT CASE WHEN is_return = 0 THEN invoice END) AS orders,
    COUNT(DISTINCT CASE WHEN is_return = 0 AND customer_id <> '' THEN customer_id END) AS customers,
    SUM(CASE WHEN is_service = 1 AND is_return = 0 THEN line_value ELSE 0 END) AS service_sales
FROM transactions
GROUP BY substr(invoice_date, 1, 7)
ORDER BY month;
