"""统一数据口径与可独立复用的零售分析。"""
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "online_retail_ii.csv.gz"
SERVICE_PATTERN = re.compile(r"\b(?:manual|postage|fees?|bank charges?|adjustments?|commission|samples?)\b", re.I)
SERVICE_CODES = {"POST", "DOT", "M", "D", "C2", "BANK CHARGES", "AMAZONFEE", "ADJUST", "ADJUST2", "CRUK", "S"}
REQUIRED_COLUMNS = {"invoice", "stock_code", "description", "quantity", "invoice_date", "unit_price", "customer_id", "country"}


def prepare_transactions(raw):
    """从已有 UCI 摘要重新核算金额，不猜测缺失客户或删除疑似重复行。"""
    missing = REQUIRED_COLUMNS.difference(raw.columns)
    if missing:
        raise ValueError(f"交易数据缺少字段：{', '.join(sorted(missing))}")
    data = raw.copy()
    for col in ["invoice", "stock_code", "description", "customer_id", "country"]:
        data[col] = data[col].astype("string").fillna("").str.strip()
    data["invoice_date"] = pd.to_datetime(data["invoice_date"], errors="coerce")
    data["quantity"] = pd.to_numeric(data["quantity"], errors="coerce")
    data["unit_price"] = pd.to_numeric(data["unit_price"], errors="coerce")
    input_rows = len(data)
    valid = (data["invoice"].ne("") & data["country"].ne("") & data["invoice_date"].notna()
             & np.isfinite(data["quantity"]) & np.isfinite(data["unit_price"])
             & data["quantity"].ne(0) & data["unit_price"].ge(0))
    data = data.loc[valid].copy()
    stored_returns = pd.to_numeric(data.get("is_return", pd.Series(0, index=data.index)), errors="coerce").fillna(0).ne(0)
    data["is_return"] = stored_returns | data["invoice"].str.upper().str.startswith("C") | data["quantity"].lt(0)
    data["quantity"] = data["quantity"].abs()
    amount = (data["quantity"] * data["unit_price"]).round(2)
    data["line_value"] = amount.where(~data["is_return"], -amount)
    data["description"] = data["description"].replace("", "未命名商品")
    data["is_service"] = data["description"].str.contains(SERVICE_PATTERN) | data["stock_code"].str.upper().isin(SERVICE_CODES)
    data["month"] = data["invoice_date"].dt.to_period("M").astype(str)
    data = data.sort_values("invoice_date", kind="stable").reset_index(drop=True)
    data.attrs = {"input_rows": input_rows, "excluded_rows": input_rows - len(data)}
    if not data.empty:
        data.attrs.update(coverage_start=data["invoice_date"].min().normalize(), coverage_end=data["invoice_date"].max().normalize())
    return data


def load_transactions(path=DATA_FILE):
    raw = pd.read_csv(path, compression="gzip", dtype={c: "string" for c in ["invoice", "stock_code", "customer_id", "country"]})
    return prepare_transactions(raw)


def complete_months(start, end):
    """完整性来自观察窗口，月末无交易不等于缺失数据。"""
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    return [p for p in pd.period_range(start, end, freq="M") if p.start_time >= start and p.end_time.normalize() <= end]


def _monthly(data, start, end):
    sales = data.loc[~data["is_return"]]
    returns = data.loc[data["is_return"]]
    idx = pd.Index(pd.period_range(start, end, freq="M").astype(str), name="month")
    out = sales.groupby("month").agg(gross_sales=("line_value", "sum"), orders=("invoice", "nunique"))
    out = out.reindex(idx, fill_value=0)
    out["return_value"] = -returns.groupby("month")["line_value"].sum().reindex(idx, fill_value=0)
    out["net_sales"] = out["gross_sales"] - out["return_value"]
    out["customers"] = sales.loc[sales["customer_id"].ne("")].groupby("month")["customer_id"].nunique().reindex(idx, fill_value=0)
    out["service_sales"] = sales.loc[sales["is_service"]].groupby("month")["line_value"].sum().reindex(idx, fill_value=0)
    out["aov"] = out["gross_sales"].div(out["orders"].replace(0, np.nan))
    out["return_rate"] = out["return_value"].div(out["gross_sales"].replace(0, np.nan))
    out = out.reset_index()
    out["month"] = pd.to_datetime(out["month"])
    out["complete"] = out["month"].dt.to_period("M").isin(complete_months(start, end))
    return out


def _yoy(monthly):
    current = monthly.loc[monthly["complete"]].copy()
    current["prior_month"] = current["month"] - pd.DateOffset(years=1)
    prior = current[["month", "gross_sales", "orders", "aov"]].rename(columns={"month": "prior_month", "gross_sales": "prior_sales", "orders": "prior_orders", "aov": "prior_aov"})
    out = current.merge(prior, on="prior_month", how="inner")
    for label, now, before in [("sales", "gross_sales", "prior_sales"), ("orders", "orders", "prior_orders"), ("aov", "aov", "prior_aov")]:
        out[f"{label}_change_pct"] = out[now].div(out[before].replace(0, np.nan)).sub(1)
    out["sales_change"] = out["gross_sales"] - out["prior_sales"]
    out["volume_effect"] = (out["orders"] - out["prior_orders"]) * out["prior_aov"]
    out["basket_effect"] = out["orders"] * (out["aov"] - out["prior_aov"])
    decomposable = out["orders"].gt(0) & out["prior_orders"].gt(0)
    out["volume_effect"] = out["volume_effect"].where(decomposable, 0.0)
    out["basket_effect"] = out["basket_effect"].where(decomposable, 0.0)
    out["zero_order_effect"] = out["sales_change"].where(~decomposable, 0.0)
    return out


def _products(sales):
    items = sales.loc[~sales["is_service"]].groupby("stock_code", as_index=False).agg(
        description=("description", "last"), sales=("line_value", "sum"), quantity=("quantity", "sum"), orders=("invoice", "nunique"))
    items["sales"] = items["sales"].round(2)
    items = items.sort_values(["sales", "stock_code"], ascending=[False, True]).reset_index(drop=True)
    total = items["sales"].sum()
    items["sales_share"] = items["sales"] / total if total else 0.0
    items["cumulative_share"] = items["sales_share"].cumsum()
    items["product_share"] = (items.index + 1) / len(items) if len(items) else 0.0
    return items


def _customers(sales, history, end):
    identified = sales.loc[sales["customer_id"].ne("")]
    customers = identified.groupby("customer_id", as_index=False).agg(sales=("line_value", "sum"), orders=("invoice", "nunique"))
    frequency = customers.groupby("orders", as_index=False).agg(customers=("customer_id", "count"))
    frequency["share"] = frequency["customers"].div(frequency["customers"].sum())
    history_sales = history.loc[~history["is_return"] & history["customer_id"].ne("")]
    first = history_sales.groupby("customer_id")["invoice_date"].min()
    full = history_sales.copy()
    full["cohort"] = full["customer_id"].map(first).dt.to_period("M")
    full["order_month"] = full["invoice_date"].dt.to_period("M")
    observed_end = pd.Timestamp(end).to_period("M")
    # 不完整的末月不用于留存；未来尚未观察到的格子保持空白。
    if pd.Timestamp(end).day != pd.Timestamp(end).days_in_month:
        observed_end -= 1
    full = full.loc[full["order_month"] <= observed_end].copy()
    if full.empty:
        return customers, frequency, pd.DataFrame(), pd.Series(dtype="int64")
    full["age"] = (full["order_month"].dt.year - full["cohort"].dt.year) * 12 + full["order_month"].dt.month - full["cohort"].dt.month
    counts = full.groupby(["cohort", "age"])["customer_id"].nunique().unstack(fill_value=0)
    earliest = counts.index.min()
    max_age = (observed_end.year - earliest.year) * 12 + observed_end.month - earliest.month
    counts = counts.reindex(columns=range(max_age + 1), fill_value=0)
    sizes = counts[0].copy()
    retention = counts.div(sizes, axis=0)
    for cohort in retention.index:
        retention.loc[cohort, [age for age in retention.columns if cohort + age > observed_end]] = np.nan
    retention.index = retention.index.astype(str)
    sizes.index = sizes.index.astype(str)
    return customers, frequency, retention, sizes


def run_analysis(data, coverage_start=None, coverage_end=None, history=None):
    """返回月度、同比、商品、客户、留存和数据质量结果表。"""
    start = coverage_start if coverage_start is not None else data.attrs.get("coverage_start", data["invoice_date"].min())
    end = coverage_end if coverage_end is not None else data.attrs.get("coverage_end", data["invoice_date"].max())
    if pd.isna(start) or pd.isna(end):
        start = end = pd.Timestamp("2000-01-01")
    history = data if history is None else history
    monthly = _monthly(data, start, end)
    sales = data.loc[~data["is_return"]]
    returns = data.loc[data["is_return"]]
    customers, frequency, retention, sizes = _customers(sales, history, end)
    quality = pd.DataFrame([{
        "输入商品行": data.attrs.get("input_rows", len(data)), "有效商品行": len(data),
        "剔除无效行": data.attrs.get("excluded_rows", 0), "销售商品行": len(sales), "退货商品行": len(returns),
        "缺失客户编号行": int(data["customer_id"].eq("").sum()), "服务及调整类行": int(data["is_service"].sum()),
        "零单价行": int(data["unit_price"].eq(0).sum()), "销售金额": sales["line_value"].sum(),
        "退货金额": -returns["line_value"].sum(), "净销售额": data["line_value"].sum(),
    }])
    yoy = _yoy(monthly)
    product_change = pd.DataFrame()
    if not yoy.empty:
        year = yoy["month"].dt.year.max()
        pairs = yoy.loc[yoy["month"].dt.year.eq(year)]
        goods = sales.loc[~sales["is_service"]]
        current = _products(goods.loc[goods["month"].isin(pairs["month"].dt.strftime("%Y-%m"))])[["stock_code", "description", "sales"]]
        prior = _products(goods.loc[goods["month"].isin(pairs["prior_month"].dt.strftime("%Y-%m"))])[["stock_code", "description", "sales"]]
        product_change = current.merge(prior, on="stock_code", how="outer", suffixes=("", "_prior"))
        product_change["description"] = product_change["description"].fillna(product_change["description_prior"])
        product_change[["sales", "sales_prior"]] = product_change[["sales", "sales_prior"]].fillna(0)
        product_change["change"] = product_change["sales"] - product_change["sales_prior"]
        product_change = product_change.sort_values("change", ascending=False)
    return {"monthly": monthly, "yoy": yoy, "products": _products(sales), "product_change": product_change,
            "customers": customers, "frequency": frequency, "retention": retention,
            "cohort_sizes": sizes, "quality": quality}


def run_sql(data):
    """将同一清洗明细写入临时 SQLite，执行项目内所有 SQL。"""
    columns = ["invoice", "stock_code", "description", "invoice_date", "customer_id", "quantity", "line_value", "is_return", "is_service"]
    with sqlite3.connect(":memory:") as connection:
        data[columns].to_sql("transactions", connection, index=False, chunksize=10000)
        return {path.stem: pd.read_sql_query(path.read_text(encoding="utf-8"), connection) for path in sorted((ROOT / "sql").glob("*.sql"))}
