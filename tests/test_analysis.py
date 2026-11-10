from io import BytesIO

import numpy as np
import pandas as pd

from src.analysis import complete_months, load_transactions, prepare_transactions, run_analysis, run_sql
from src.reproduce import verify_sql


def transactions(rows):
    """发票、商品、描述、数量、日期、单价、客户、地区。"""
    raw = pd.DataFrame(rows, columns=["invoice", "stock_code", "description", "quantity", "invoice_date", "unit_price", "customer_id", "country"])
    return prepare_transactions(raw)


def sample():
    return transactions([
        ["100", "A", "MUG", 2, "2010-01-01", 10, "1", "UK"],
        ["101", "POST", "POSTAGE", 1, "2010-01-31", 5, "1", "UK"],
        ["102", "A", "MUG NEW NAME", 1, "2011-01-01", 10, "", "UK"],
        ["C103", "A", "MUG", 1, "2011-01-10", 10, "1", "UK"],
        ["104", "A", "MUG", 2, "2011-01-31", 10, "1", "UK"],
    ])


def test_normalization_returns_services_and_invalid_rows():
    data = transactions([
        ["100", "A", "COFFEE MUG", 2, "2011-01-01", 5, None, "UK"],
        ["101", "A", "COFFEE MUG", -1, "2011-01-02", 5, "1", "UK"],
        ["C102", "M", "Manual", 1, "2011-01-03", 2, "1", "UK"],
        ["103", "A", "BAD", 1, "invalid", 5, "1", "UK"],
        ["104", "A", "BAD", 0, "2011-01-01", 5, "1", "UK"],
        ["105", "A", "BAD", 1, "2011-01-01", -5, "1", "UK"],
    ])
    assert data.attrs["excluded_rows"] == 3
    assert data["line_value"].tolist() == [10, -5, -2]
    assert data["customer_id"].tolist() == ["", "1", "1"]
    assert data["is_service"].tolist() == [False, False, True]


def test_bundled_return_flags_survive_positive_quantity():
    data = sample()
    raw = data.drop(columns=["is_service", "month"])
    raw.loc[raw["invoice"].eq("101"), "is_return"] = True
    buffer = BytesIO()
    raw.to_csv(buffer, index=False, compression="gzip")
    buffer.seek(0)
    loaded = load_transactions(buffer)
    assert loaded.loc[loaded["invoice"].eq("101"), "line_value"].iloc[0] == -5


def test_sales_identity_products_and_missing_customers():
    result = run_analysis(sample())
    q = result["quality"].iloc[0]
    assert q["净销售额"] == 45
    assert q["销售金额"] == 55
    assert q["退货金额"] == 10
    assert q["缺失客户编号行"] == 1
    assert result["products"]["stock_code"].tolist() == ["A"]
    assert result["products"]["sales"].tolist() == [50]
    assert result["customers"]["customer_id"].tolist() == ["1"]
    assert result["customers"]["orders"].tolist() == [3]
    monthly = result["monthly"]
    np.testing.assert_allclose(monthly["gross_sales"], (monthly["orders"] * monthly["aov"]).fillna(0))


def test_month_coverage_is_independent_of_last_sale_date():
    assert [str(p) for p in complete_months("2010-01-04", "2010-04-09")] == ["2010-02", "2010-03"]
    data = transactions([["1", "A", "MUG", 1, "2010-01-05", 10, "1", "UK"]])
    result = run_analysis(data, "2010-01-01", "2010-01-31")
    assert result["monthly"]["complete"].tolist() == [True]
    partial = run_analysis(data, "2010-01-05", "2010-01-20")
    assert partial["monthly"]["complete"].tolist() == [False]


def test_yoy_exact_change_and_partial_exclusion():
    result = run_analysis(sample())
    yoy = result["yoy"]
    assert len(yoy) == 1
    row = yoy.iloc[0]
    assert row["sales_change"] == 5
    assert row["volume_effect"] + row["basket_effect"] == 5
    assert row["sales_change_pct"] == .2 or np.isclose(row["sales_change_pct"], .2)
    assert run_analysis(sample(), "2010-01-01", "2011-01-20")["yoy"].empty


def test_cohort_future_is_nan_and_observed_absence_is_zero():
    history = transactions([
        ["1", "A", "MUG", 1, "2010-01-01", 10, "1", "UK"],
        ["2", "A", "MUG", 1, "2010-02-01", 10, "2", "UK"],
        ["3", "A", "MUG", 1, "2010-03-01", 10, "1", "UK"],
        ["4", "A", "MUG", 1, "2010-03-31", 10, "3", "UK"],
    ])
    result = run_analysis(history)
    cohort = result["retention"]
    assert cohort.loc["2010-01", 0] == 1
    assert cohort.loc["2010-01", 1] == 0
    assert cohort.loc["2010-01", 2] == 1
    assert np.isnan(cohort.loc["2010-03", 1])
    # 日期范围变化不能把老客户重新当作新客户。
    selected = history.loc[history["invoice_date"] >= "2010-03-01"]
    again = run_analysis(selected, "2010-03-01", "2010-03-31", history=history)
    assert again["retention"].loc["2010-01", 2] == 1


def test_empty_anonymous_and_return_only_ranges():
    data = sample()
    result = run_analysis(data.iloc[:0])
    assert result["customers"].empty
    assert result["retention"].empty
    anonymous = data.loc[data["customer_id"].eq("")]
    assert run_analysis(anonymous)["customers"].empty
    returns = data.loc[data["is_return"]]
    result = run_analysis(returns)
    assert result["quality"].iloc[0]["净销售额"] == -10
    assert result["monthly"]["aov"].isna().all()


def test_sql_matches_python_and_product_change():
    data = sample()
    result = run_analysis(data)
    verify_sql(data, result, run_sql(data))
    change = result["product_change"].set_index("stock_code")
    assert change.loc["A", "change"] == 10


def test_no_repurchase_still_has_observed_zero_columns():
    data = transactions([
        ["1", "A", "MUG", 1, "2010-01-01", 10, "1", "UK"],
        ["2", "A", "MUG", 1, "2010-02-01", 10, "2", "UK"],
        ["3", "A", "MUG", 1, "2010-03-31", 10, "3", "UK"],
    ])
    cohort = run_analysis(data)["retention"]
    assert cohort.loc["2010-01", 2] == 0
    assert cohort.loc["2010-02", 1] == 0
    assert np.isnan(cohort.loc["2010-03", 1])


def test_zero_order_comparison_has_separate_effect():
    data = transactions([
        ["C1", "A", "MUG", -1, "2010-01-01", 10, "1", "UK"],
        ["2", "A", "MUG", 2, "2011-01-31", 10, "1", "UK"],
    ])
    row = run_analysis(data)["yoy"].iloc[0]
    assert row["sales_change"] == 20
    assert row["volume_effect"] + row["basket_effect"] + row["zero_order_effect"] == 20
