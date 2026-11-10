"""运行：python -m src.reproduce [--verify-sql]。"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import load_transactions, run_analysis, run_sql


def findings(result):
    monthly = result["monthly"]
    complete = monthly.loc[monthly["complete"]]
    lines = []
    if not complete.empty:
        peak = complete.loc[complete["gross_sales"].idxmax()]
        lines.append(f"{peak['month']:%Y-%m} 为完整月份中的销售高点，正向销售额 £{peak['gross_sales']:,.0f}，订单 {peak['orders']:,.0f} 笔。")
    yoy = result["yoy"]
    valid = yoy.loc[yoy["prior_sales"].gt(0) & yoy["prior_orders"].gt(0) & yoy["orders"].gt(0)]
    if not valid.empty:
        latest_year = valid["month"].dt.year.max()
        valid = valid.loc[valid["month"].dt.year.eq(latest_year)]
        before, after = valid["prior_sales"].sum(), valid["gross_sales"].sum()
        lines.append(f"{latest_year} 年 {len(valid)} 个可比完整月销售额同比 {after / before - 1:+.1%}；订单量变化贡献 £{valid['volume_effect'].sum():+,.0f}，客单价变化贡献 £{valid['basket_effect'].sum():+,.0f}。")
    products = result["products"]
    if not products.empty:
        lines.append(f"前 20 个商品贡献普通商品销售额的 {products.head(20)['sales_share'].sum():.1%}；统计按商品编码合并，服务和调整类记录另计。")
    customers = result["customers"]
    if not customers.empty:
        lines.append(f"筛选期内 {len(customers):,} 位可识别客户中，{customers['orders'].gt(1).mean():.1%} 有两张及以上销售发票。")
    return lines


def verify_sql(data, result, sql_tables):
    """比较每月金额/去重计数、商品贡献与已观察 cohort 格。"""
    sql = sql_tables["monthly_sales"].set_index("month")
    python = result["monthly"].assign(month=lambda x: x["month"].dt.strftime("%Y-%m-%d")).set_index("month")
    for col in ["gross_sales", "return_value", "net_sales", "orders", "customers", "service_sales"]:
        np.testing.assert_allclose(sql[col], python.loc[sql.index, col], rtol=1e-9, atol=0.01)
    products = sql_tables["product_contribution"].set_index("stock_code")
    python_products = result["products"].set_index("stock_code")
    for col in ["sales", "quantity", "orders", "sales_share", "cumulative_share"]:
        np.testing.assert_allclose(products[col], python_products.loc[products.index, col], rtol=1e-9, atol=0.000001)
    for row in sql_tables["customer_cohort"].itertuples():
        retention = result["retention"]
        if row.cohort in retention.index and row.age in retention.columns:
            expected = retention.loc[row.cohort, row.age]
            if pd.notna(expected):
                np.testing.assert_allclose(row.retention, expected)


def main():
    parser = argparse.ArgumentParser(description="生成零售分析结果与报告")
    parser.add_argument("--verify-sql", action="store_true", help="执行 SQLite 并核对 Python 结果")
    parser.add_argument("--output", default="outputs", help="输出目录，默认 outputs")
    args = parser.parse_args()
    data = load_transactions()
    result = run_analysis(data)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for name, table in result.items():
        if name in {"retention", "cohort_sizes"}:
            table.to_csv(output / f"{name}.csv", encoding="utf-8-sig")
        else:
            table.to_csv(output / f"{name}.csv", index=False, encoding="utf-8-sig")
    report = "# 零售分析结果\n\n" + "\n\n".join(findings(result))
    report += "\n\n## 统计限制\n\n首购仅指观察窗口内首次交易。退货与原发票未逐单配对；本期退货金额不能视为本期销售订单的最终退货率。价格、费用、活动及毛利信息缺失，不能估计利润或验证运营建议的效果。\n"
    (output / "report.md").write_text(report, encoding="utf-8")
    if args.verify_sql:
        sql = run_sql(data)
        verify_sql(data, result, sql)
        for name, table in sql.items():
            table.to_csv(output / f"sql_{name}.csv", index=False, encoding="utf-8-sig")
        print("SQL 与 Python 的月度、商品贡献及已观察 cohort 结果一致。")
    print(f"已生成 {output.as_posix()}/report.md 及分析结果表。")
    print("\n".join(findings(result)))


if __name__ == "__main__":
    main()
