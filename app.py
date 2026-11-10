"""零售分析展示入口。指标计算见 src/analysis.py。"""
from pathlib import Path
import textwrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.analysis import DATA_FILE, load_transactions, run_analysis
from src.reproduce import findings

ORANGE, BROWN, GREEN, LIGHT = "#B54F2B", "#79654F", "#557568", "#E9C999"
CHAPTERS = ["销售增长", "商品结构", "客户复购", "数据与复现"]
st.set_page_config(page_title="零售销售分析", page_icon=":material/query_stats:", layout="wide", initial_sidebar_state="collapsed")


@st.cache_resource(show_spinner="读取交易数据…", max_entries=1)
def source_data(version):
    return load_transactions()


@st.cache_data(show_spinner="计算分析结果…", max_entries=4)
def analysis_result(start, end, countries, version):
    source = source_data(version)
    mask = source["country"].isin(countries)
    # 保留筛选起点以前的历史，避免老客户被重新归为新 cohort。
    history = source.loc[mask & source["invoice_date"].lt(pd.Timestamp(end) + pd.Timedelta(days=1))].copy()
    selected = history.loc[history["invoice_date"].ge(pd.Timestamp(start))].copy()
    selected.attrs = {"input_rows": len(selected), "excluded_rows": 0}
    return run_analysis(selected, start, end, history=history)


def draw(fig, key, height=390, left=68):
    fig.update_layout(
        template="plotly_white", height=height,
        paper_bgcolor="#FFFDFA", plot_bgcolor="#FFFDFA",
        font={"family": "Microsoft YaHei, Segoe UI, sans-serif", "size": 12, "color": "#493E34"},
        margin={"l": left, "r": 42, "t": 28, "b": 66},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hoverlabel={"bgcolor": "#FFFDFA"},
    )
    fig.update_xaxes(automargin=True, title_standoff=18, showgrid=False, tickfont_size=11)
    fig.update_yaxes(automargin=True, title_standoff=18, gridcolor="#EEE7DD", zerolinecolor="#DCCDBA")
    st.plotly_chart(fig, key=key, width="stretch", theme=None, config={"displaylogo": False, "responsive": True})


def pct_columns(names):
    return {name: st.column_config.NumberColumn(name, format="percent", width="small") for name in names}


def show_growth(result):
    monthly, quality = result["monthly"], result["quality"].iloc[0]
    cols = st.columns(4)
    cols[0].metric("期间净销售额", f"£{quality['净销售额']:,.0f}")
    cols[1].metric("销售发票数", f"{monthly['orders'].sum():,.0f}")
    orders = monthly["orders"].sum()
    cols[2].metric("正向销售客单价", f"£{quality['销售金额'] / orders:,.2f}" if orders else "—")
    cols[3].metric("期间退货金额", f"£{quality['退货金额']:,.0f}")
    st.write(findings(result)[0] if findings(result) else "")

    with st.container(border=True):
        st.subheader("月度销售")
        fig = go.Figure()
        x = monthly["month"].dt.strftime("%Y-%m")
        fig.add_scatter(x=x, y=monthly["gross_sales"], name="正向销售额", mode="lines", line={"color": ORANGE, "width": 3})
        fig.add_scatter(x=x, y=monthly["net_sales"], name="扣退货后净额", mode="lines", line={"color": GREEN, "width": 2})
        partial = monthly.loc[~monthly["complete"]]
        if not partial.empty:
            fig.add_scatter(x=partial["month"].dt.strftime("%Y-%m"), y=partial["gross_sales"], mode="markers", name="非完整月份", marker={"color": ORANGE, "symbol": "circle-open", "size": 11})
        fig.update_layout(xaxis={"title": "月份", "type": "category", "nticks": 12}, yaxis_title="金额（英镑）", hovermode="x unified")
        draw(fig, "sales_trend")

    yoy = result["yoy"]
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("增长贡献")
        if yoy.empty:
            st.info("当前日期范围没有两年共同的完整月份。")
        else:
            comparison = yoy.loc[yoy["month"].dt.year.eq(yoy["month"].dt.year.max())]
            st.caption(f"{comparison['month'].dt.year.max()} 年，共 {len(comparison)} 个可比月 · 正向销售额")
            volume, basket, residual, total = comparison[["volume_effect", "basket_effect", "zero_order_effect", "sales_change"]].sum()
            names, values = ["订单量变化", "客单价变化"], [volume, basket]
            if residual:
                names.append("零订单月份")
                values.append(residual)
            names.append("销售额变化")
            values.append(total)
            fig = go.Figure(go.Waterfall(
                x=names, measure=["relative"] * (len(values) - 1) + ["total"],
                y=values, text=[f"£{v:+,.0f}" for v in values], textposition="outside",
                increasing={"marker": {"color": GREEN}}, decreasing={"marker": {"color": ORANGE}}, totals={"marker": {"color": BROWN}},
            ))
            fig.update_layout(yaxis_title="变化金额（英镑）", showlegend=False)
            draw(fig, "growth_bridge", 370)
            if residual:
                st.caption("任一年无销售订单的月份无法比较客单价，其金额变化单列。")
    with right, st.container(border=True):
        st.subheader("年内月度分布")
        fig = go.Figure()
        complete = monthly.loc[monthly["complete"]].copy()
        for year, table in complete.groupby(complete["month"].dt.year):
            fig.add_scatter(x=table["month"].dt.month, y=table["gross_sales"], mode="lines+markers", name=str(year))
        fig.update_layout(xaxis={"title": "月份", "tickmode": "array", "tickvals": list(range(1, 13))}, yaxis_title="正向销售额（英镑）", colorway=[LIGHT, BROWN, ORANGE])
        draw(fig, "seasonality", 370)

    with st.expander("月度指标与同比明细"):
        table = monthly[["month", "gross_sales", "net_sales", "orders", "aov", "complete"]].rename(columns={"month": "月份", "gross_sales": "正向销售额", "net_sales": "净销售额", "orders": "订单数", "aov": "客单价", "complete": "完整月"})
        st.dataframe(table, hide_index=True, width="stretch")
        if not yoy.empty:
            table = yoy[["month", "sales_change_pct", "orders_change_pct", "aov_change_pct"]].rename(columns={"month": "月份", "sales_change_pct": "销售额同比", "orders_change_pct": "订单数同比", "aov_change_pct": "客单价同比"})
            st.dataframe(table, hide_index=True, column_config=pct_columns(["销售额同比", "订单数同比", "客单价同比"]))


def show_products(result):
    products = result["products"]
    if products.empty or not products["sales"].sum():
        st.info("当前范围内没有普通商品销售。")
        return
    top_share = products.head(20)["sales_share"].sum()
    needed = int((products["cumulative_share"] < .8).sum()) + 1
    st.write(f"前 20 个商品贡献普通商品销售额的 {top_share:.1%}。累计覆盖 80% 销售额需要 {needed:,} 个商品，占在售商品的 {needed / len(products):.1%}。")
    left, right = st.columns([1.3, 1])
    with left, st.container(border=True):
        st.subheader("销售额前 12 商品")
        top = products.head(12).iloc[::-1].copy()
        labels = ["<br>".join(textwrap.wrap(str(v), 32)) for v in top["description"]]
        fig = go.Figure(go.Bar(y=top["stock_code"], x=top["sales"], orientation="h", marker_color=ORANGE, customdata=top[["description", "orders"]], hovertemplate="%{customdata[0]}<br>销售额 £%{x:,.2f}<br>订单数 %{customdata[1]:,}<extra></extra>"))
        fig.update_layout(xaxis_title="正向销售额（英镑）", yaxis={"tickmode": "array", "tickvals": top["stock_code"].tolist(), "ticktext": labels, "type": "category"})
        draw(fig, "top_products", 650, left=240)
    with right, st.container(border=True):
        st.subheader("商品集中度")
        fig = go.Figure(go.Scatter(x=[0.0] + products["product_share"].tolist(), y=[0.0] + products["cumulative_share"].tolist(), mode="lines", line={"color": ORANGE, "width": 3}, fill="tozeroy", fillcolor="rgba(181,79,43,.08)"))
        fig.add_hline(y=.8, line_dash="dot", line_color=BROWN)
        fig.update_layout(xaxis={"title": "商品累计占比（按销售额降序）", "tickformat": ".0%"}, yaxis={"title": "销售额累计占比", "tickformat": ".0%", "range": [0, 1.05]}, showlegend=False)
        draw(fig, "pareto", 420)
        st.caption("按商品编码合并；邮费、手续费及人工调整等记录不计入商品贡献。")
    change = result["product_change"]
    if not change.empty:
        st.subheader("可比月份的商品增减")
        st.caption("与增长贡献图使用相同的同比月份，服务与调整类金额另计。")
        table = pd.concat([change.head(5), change.tail(5)]).drop_duplicates("stock_code")
        table = table[["stock_code", "description", "sales_prior", "sales", "change"]].rename(columns={"stock_code": "商品编码", "description": "商品", "sales_prior": "上年销售额", "sales": "本年销售额", "change": "销售额增减"})
        st.dataframe(table, hide_index=True, width="stretch")


def show_customers(result):
    customers = result["customers"]
    if customers.empty:
        st.info("当前范围内没有可识别客户编号。")
        return
    count = customers["orders"].gt(1).sum()
    cols = st.columns(3)
    cols[0].metric("可识别客户", f"{len(customers):,}")
    cols[1].metric("期间复购客户", f"{count:,}")
    cols[2].metric("期间复购占比", f"{count / len(customers):.1%}")
    with st.container(border=True):
        st.subheader("期间购买频次")
        groups = pd.cut(customers["orders"], [0, 1, 2, 5, float("inf")], labels=["1 单", "2 单", "3–5 单", "6 单及以上"])
        freq = groups.value_counts(sort=False)
        fig = go.Figure(go.Bar(x=freq.index.astype(str), y=freq.values, text=freq.values, textposition="outside", cliponaxis=False, marker_color=[LIGHT, BROWN, GREEN, ORANGE]))
        fig.update_layout(xaxis_title="销售发票数", yaxis={"title": "客户数", "range": [0, max(freq.max() * 1.18, 1)]})
        draw(fig, "frequency", 350)
    retention = result["retention"]
    with st.container(border=True):
        st.subheader("首购月份与后续回购")
        st.caption("首购为所选地区内首次观察到的销售；使用筛选起点以前的历史。空白格尚未观察，不完整末月不计算。")
        if retention.empty:
            st.info("当前观察窗口没有完整的客户购买月份。")
        else:
            display = retention.iloc[:, :13]
            fig = go.Figure(go.Heatmap(z=display.to_numpy(), x=[f"第 {i} 月" for i in display.columns], y=display.index,
                                      zmin=0, zmax=1, colorscale=[[0, "#FAF0DE"], [.35, "#D6924A"], [1, "#8F3A25"]],
                                      hoverongaps=False, colorbar={"tickformat": ".0%", "title": "回购比例"},
                                      hovertemplate="首购月 %{y}<br>%{x}：%{z:.1%}<extra></extra>"))
            fig.update_layout(xaxis={"title": "距首次观察购买的月份"}, yaxis={"title": "首次观察购买月份", "autorange": "reversed", "type": "category", "dtick": 1})
            draw(fig, "cohort", max(400, len(display) * 24 + 110), left=90)
            sizes = result["cohort_sizes"].rename("客户数").to_frame()
            with st.expander("首购客群规模"):
                st.dataframe(sizes, width="stretch")


def show_data(result, source):
    monthly = result["monthly"]
    with st.container(border=True):
        st.subheader("月度退货金额占比")
        fig = go.Figure(go.Bar(x=monthly["month"].dt.strftime("%Y-%m"), y=monthly["return_rate"], marker_color=BROWN))
        fig.update_layout(xaxis={"title": "月份", "type": "category", "nticks": 12}, yaxis={"title": "退货金额 / 正向销售额", "tickformat": ".0%"})
        draw(fig, "returns")
    st.caption("退货按发生月份统计，未与原发票配对；它是期间金额比，不是销售订单最终退货率。")
    st.subheader("数据检查")
    quality = result["quality"].T.reset_index()
    quality.columns = ["项目", "数值"]
    st.dataframe(quality, hide_index=True, width="stretch")
    st.caption(f"项目输入摘要 {len(source):,} 行；加载时剔除 {source.attrs.get('excluded_rows', 0):,} 条无效记录。输入摘要已在此前转换中处理过，不能视为未加工的原始工作簿。")
    with st.expander("字段、口径与限制"):
        st.markdown("""
        - **金额**：数量绝对值 × 单价；取消或退货记负数。净销售额 = 正向销售额 − 退货金额。
        - **客单价**：正向销售额 ÷ 不同销售发票数。增长拆解顺序为先订单量、后客单价，不是因果归因。
        - **商品**：按商品编码合并；服务、邮费、手续费、折扣和调整类记录保留在财务金额中，从商品分析排除。规则见分析模块。
        - **客户**：空编号不参与客户指标。cohort 使用所选地区内可观察历史，不等于真实首次购买。
        - **时间**：完整月由数据覆盖窗口和所选日期共同决定，月末没有交易不会使月份失效。
        - **限制**：无成本、营销活动、流量或实验数据，不计算利润、转化率或活动效果。
        """)
    st.subheader("结果下载")
    tables = {"月度指标": "monthly", "同比拆解": "yoy", "商品贡献": "products", "客户汇总": "customers", "客户留存": "retention", "数据检查": "quality"}
    selected_table = st.selectbox("结果表", list(tables))
    table = result[tables[selected_table]]
    st.download_button("下载 CSV", table.to_csv(index=tables[selected_table] == "retention").encode("utf-8-sig"), file_name=f"{tables[selected_table]}.csv", mime="text/csv")
    with st.expander("复现 SQL"):
        st.caption("同一份清洗明细导入内存 SQLite；复现命令会核对月度、商品及已观察留存格。")
        st.code("python -m src.reproduce --verify-sql", language="bash")
        sql_files = sorted((Path(__file__).parent / "sql").glob("*.sql"))
        query = st.selectbox("查询文件", [p.name for p in sql_files])
        st.code(next(p for p in sql_files if p.name == query).read_text(encoding="utf-8"), language="sql")


st.title("零售销售分析")
st.caption("UCI Online Retail II  /  2009—2011  /  英镑")
try:
    version = (DATA_FILE.stat().st_mtime_ns, (Path(__file__).parent / "src" / "analysis.py").stat().st_mtime_ns)
    source = source_data(version)
except (OSError, ValueError, pd.errors.EmptyDataError) as exc:
    st.error(f"无法读取项目数据：{exc}")
    st.stop()
min_date, max_date = source["invoice_date"].min().date(), source["invoice_date"].max().date()
with st.form("filters", border=False):
    left, right = st.columns([1, 2])
    with left:
        dates = st.date_input("交易日期", (min_date, max_date), min_value=min_date, max_value=max_date, format="YYYY-MM-DD")
    with right:
        countries = st.multiselect("国家 / 地区", ["全部地区"] + sorted(source["country"].unique().tolist()), default=["全部地区"])
    st.form_submit_button("更新分析", type="primary")
if len(dates) != 2 or not countries:
    st.info("请选择完整日期范围和至少一个地区。")
    st.stop()
start, end = dates
chosen = tuple(sorted(source["country"].unique())) if "全部地区" in countries else tuple(sorted(countries))
if not (source["invoice_date"].between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)) & source["country"].isin(chosen)).any():
    st.info("该日期与地区范围内没有交易。")
    st.stop()
result = analysis_result(start, end, chosen, version)
st.caption(f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d} · {'全部地区' if '全部地区' in countries else '、'.join(countries)}")
chapter = st.segmented_control("分析章节", CHAPTERS, default=CHAPTERS[0], selection_mode="single", label_visibility="collapsed")
if chapter == "销售增长":
    show_growth(result)
elif chapter == "商品结构":
    show_products(result)
elif chapter == "客户复购":
    show_customers(result)
elif chapter == "数据与复现":
    show_data(result, source)
