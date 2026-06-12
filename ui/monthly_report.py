import plotly.express as px
import streamlit as st

from services.reporting import (
    aggregate_expenses,
    aggregate_income,
    calculate_summary,
    generate_ai_analysis,
    get_top_spending,
    prepare_report_df,
)


def render_monthly_report(history_for_report, client):
    st.subheader("月度报告")

    if history_for_report.empty:
        st.info("暂无历史交易，保存账单后可生成月报")
        return

    report_df = prepare_report_df(history_for_report)

    if report_df.empty:
        st.info("暂无有效日期的交易，无法生成月报")
        return

    months = sorted(report_df["Month"].dropna().unique().tolist(), reverse=True)
    selected_month = st.selectbox(
        "选择月份",
        months,
        key="monthly_report_month"
    )
    month_df = report_df[report_df["Month"] == selected_month].copy()

    income, expense, net, count = calculate_summary(month_df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("本月真实收入", f"${income:,.2f}")
    col2.metric("本月真实支出", f"${expense:,.2f}")
    col3.metric("本月净现金流", f"${net:,.2f}")
    col4.metric("本月交易数量", count)

    expense_df, expense_summary = aggregate_expenses(month_df)

    st.subheader("本月支出分类")
    if expense_summary.empty:
        st.info("本月没有真实支出记录")
    else:
        st.dataframe(expense_summary, width="stretch")

        for category in expense_summary["Category"]:
            detail = expense_df[expense_df["Category"] == category].copy()
            total = detail["Amount"].sum()
            with st.expander(f"{category} - ${total:,.2f}"):
                show_cols = [
                    c for c in ["Date", "Description", "Amount", "SourceFile"]
                    if c in detail.columns
                ]
                st.dataframe(detail[show_cols], width="stretch")

        fig_month_expense = px.bar(
            expense_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title=f"{selected_month} 支出分类"
        )
        st.plotly_chart(
            fig_month_expense,
            width="stretch",
            key=f"month_expense_{selected_month}"
        )

    _income_df, income_summary = aggregate_income(month_df)

    st.subheader("本月收入分类")
    if income_summary.empty:
        st.info("本月没有收入记录")
    else:
        st.dataframe(income_summary, width="stretch")

        fig_month_income = px.bar(
            income_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title=f"{selected_month} 收入分类"
        )
        st.plotly_chart(
            fig_month_income,
            width="stretch",
            key=f"month_income_{selected_month}"
        )

    st.subheader("本月主要资金流出")
    outflow_top10 = get_top_spending(month_df)

    if outflow_top10.empty:
        st.info("本月没有资金流出")
    else:
        show_cols = [
            c for c in [
                "Date",
                "Description",
                "Amount",
                "Category",
                "SourceFile"
            ]
            if c in outflow_top10.columns
        ]
        st.dataframe(outflow_top10[show_cols], width="stretch")

    if st.button("生成AI财务分析", key=f"ai_report_{selected_month}"):
        with st.spinner("AI正在分析本月财务..."):
            analysis = generate_ai_analysis(
                client,
                selected_month,
                income,
                expense,
                net,
                income_summary,
                expense_summary,
                outflow_top10,
            )

            st.subheader("🤖 AI财务分析")
            st.write(analysis)
