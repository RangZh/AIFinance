import pandas as pd
import plotly.express as px
import streamlit as st

from services.database import load_user_transactions
from services.reporting import (
    aggregate_expenses,
    aggregate_income,
    build_monthly_cashflow,
    calculate_summary,
)
from transaction_model import normalize_date


def normalize_history_df(history_df):
    if history_df is None or history_df.empty:
        return pd.DataFrame()

    df = history_df.rename(
        columns={
            "date": "Date",
            "description": "Description",
            "amount": "Amount",
            "category": "Category",
            "source_file": "SourceFile",
            "month": "Month",
            "unique_key": "UniqueKey",
        }
    )

    if "Date" in df.columns:
        df["Date"] = df["Date"].apply(normalize_date)
        df = df[df["Date"] != ""]
        df["Month"] = df["Date"].str[:7]

    if "Amount" in df.columns:
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
        df = df[df["Amount"].notna()]

    return df


def show_metric_cards(df):
    income, expense, net, count = calculate_summary(df)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("真实收入", f"${income:,.2f}")
    col2.metric("真实支出", f"${expense:,.2f}")
    col3.metric("净现金流", f"${net:,.2f}")
    col4.metric("交易数量", count)


def show_financial_summary(df, key_prefix="summary"):
    if df is None or df.empty:
        st.info("暂无数据")
        return

    df = df.copy()

    if "Amount" not in df.columns or "Category" not in df.columns:
        st.warning("数据缺少 Amount 或 Category 列，无法统计")
        return

    income, expense, net, count = calculate_summary(df)

    st.subheader("财务统计")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("真实收入", f"${income:,.2f}")
    col2.metric("真实支出", f"${expense:,.2f}")
    col3.metric("净现金流", f"${net:,.2f}")
    col4.metric("交易数量", count)

    expense_df, category_summary = aggregate_expenses(df)

    st.subheader("消费分类汇总")
    st.dataframe(category_summary, width="stretch")

    st.subheader("消费分类明细")
    for category in category_summary["Category"]:
        category_detail = expense_df[expense_df["Category"] == category].copy()
        category_total = category_detail["Amount"].sum()
        with st.expander(f"{category} - ${category_total:,.2f}"):
            show_cols = [
                c for c in ["Date", "Description", "Amount", "SourceFile"]
                if c in category_detail.columns
            ]
            st.dataframe(category_detail[show_cols], width="stretch")

    if not category_summary.empty:
        fig_expense = px.bar(
            category_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title="真实消费分类图"
        )
        st.plotly_chart(
            fig_expense,
            width="stretch",
            key=f"{key_prefix}_expense_chart"
        )

    _income_df, income_summary = aggregate_income(df)

    st.subheader("收入分类汇总")
    st.dataframe(income_summary, width="stretch")

    if not income_summary.empty:
        fig_income = px.bar(
            income_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title="收入分类图"
        )
        st.plotly_chart(
            fig_income,
            width="stretch",
            key=f"{key_prefix}_income_chart"
        )

    monthly_cashflow = build_monthly_cashflow(df)
    if monthly_cashflow.empty:
        return

    st.subheader("月度现金流")
    st.dataframe(monthly_cashflow.round(2), width="stretch")

    fig_cashflow = px.line(
        monthly_cashflow,
        x="Month",
        y=["Income", "Expense", "NetCashFlow"],
        markers=True,
        title="月度现金流趋势"
    )
    st.plotly_chart(
        fig_cashflow,
        width="stretch",
        key=f"{key_prefix}_cashflow_chart"
    )


def render_dashboard(user_id, supabase):
    history_df = load_user_transactions(user_id, supabase)
    history_for_summary = normalize_history_df(history_df)

    st.subheader("账户总览")

    if history_for_summary.empty:
        st.info("你的云端账户还没有保存交易")
    else:
        show_metric_cards(history_for_summary)
        st.divider()
        st.subheader("历史趋势")
        show_financial_summary(history_for_summary, key_prefix="dashboard")

    return history_df, history_for_summary
