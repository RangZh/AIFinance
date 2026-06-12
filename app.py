import os
import pandas as pd
import plotly.express as px
import streamlit as st
from openai import OpenAI
from supabase import create_client
from services.classification import (
    CATEGORIES,
    ai_classify,
    classify_by_cloud_rules,
    classify_by_rules,
    load_rules,
    save_rule,
)
from services.database import (
    delete_transactions_by_statement,
    load_user_rules,
    load_user_transactions,
    save_cloud_rule,
    save_transactions_to_supabase,
)
from services.reporting import (
    aggregate_expenses,
    aggregate_income,
    build_monthly_cashflow,
    calculate_summary,
    generate_ai_analysis,
    get_top_spending,
    prepare_report_df,
)
from statement_parsers import read_excel_file, read_pdf_file
from transaction_model import (
    normalize_date,
)

# ======================
# 基础设置
# ======================
st.set_page_config(page_title="AI记账软件", layout="wide")

# Streamlit Cloud 的 Secrets 不一定自动进入环境变量，这里手动设置一下
if "OPENAI_API_KEY" in st.secrets:
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

client = OpenAI()

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"]
)

if "session" in st.session_state and st.session_state["session"] is not None:
    supabase.auth.set_session(
        st.session_state["session"].access_token,
        st.session_state["session"].refresh_token
    )

# Transaction contract:
# - date uses YYYY-MM-DD
# - month is always derived from date
# - positive amount is money in; negative amount is money out
# 不计入真实消费/真实收入的类别
IGNORE_CATEGORIES = {"转账还款", "投资"}

# ======================
# 用户登录 / 注册
# ======================
def login_ui():
    st.sidebar.title("用户账号")

    if "user" not in st.session_state:
        st.session_state["user"] = None

    if "session" not in st.session_state:
        st.session_state["session"] = None

    if st.session_state["user"] is None:
        email = st.sidebar.text_input("邮箱")
        password = st.sidebar.text_input("密码", type="password")

        col1, col2 = st.sidebar.columns(2)

        with col1:
            if st.button("登录"):
                try:
                    result = supabase.auth.sign_in_with_password({
                        "email": email,
                        "password": password
                    })
                    st.session_state["user"] = result.user
                    st.session_state["session"] = result.session
                    st.sidebar.success("登录成功")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"登录失败：{e}")

        with col2:
            if st.button("注册"):
                try:
                    supabase.auth.sign_up({
                        "email": email,
                        "password": password
                    })
                    st.sidebar.success("注册成功，请去邮箱确认")
                except Exception as e:
                    st.sidebar.error(f"注册失败：{e}")

        st.warning("请先登录后使用 AI记账软件")
        st.stop()

    user = st.session_state["user"]
    st.sidebar.success(f"已登录：{user.email}")

    if st.sidebar.button("退出登录"):
        st.session_state["user"] = None
        st.session_state["session"] = None
        supabase.auth.sign_out()
        st.rerun()

    return user.id


# ======================
# 数据标准化与统计
# ======================
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
            show_cols = [c for c in ["Date", "Description", "Amount", "SourceFile"] if c in category_detail.columns]
            st.dataframe(category_detail[show_cols], width="stretch")

    if not category_summary.empty:
        fig_expense = px.bar(
            category_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title="真实消费分类图"
        )
        st.plotly_chart(fig_expense, width="stretch", key=f"{key_prefix}_expense_chart")

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
        st.plotly_chart(fig_income, width="stretch", key=f"{key_prefix}_income_chart")

    monthly_cashflow = build_monthly_cashflow(df)
    if monthly_cashflow.empty:
        return

    st.subheader("月度现金流")
    st.dataframe(monthly_cashflow.round(2), width="stretch")

    if not monthly_cashflow.empty:
        fig_cashflow = px.line(
            monthly_cashflow,
            x="Month",
            y=["Income", "Expense", "NetCashFlow"],
            markers=True,
            title="月度现金流趋势"
        )
        st.plotly_chart(fig_cashflow, width="stretch", key=f"{key_prefix}_cashflow_chart")


def show_monthly_report(history_for_report, user_id):
    st.subheader("月度报告")

    if history_for_report.empty:
        st.info("暂无历史交易，保存账单后可生成月报")
        return

    report_df = prepare_report_df(history_for_report)

    if report_df.empty:
        st.info("暂无有效日期的交易，无法生成月报")
        return

    months = sorted(report_df["Month"].dropna().unique().tolist(), reverse=True)
    selected_month = st.selectbox("选择月份", months, key="monthly_report_month")

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
                show_cols = [c for c in ["Date", "Description", "Amount", "SourceFile"] if c in detail.columns]
                st.dataframe(detail[show_cols], width="stretch")

        fig_month_expense = px.bar(
            expense_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title=f"{selected_month} 支出分类"
        )
        st.plotly_chart(fig_month_expense, width="stretch", key=f"month_expense_{selected_month}")

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
        st.plotly_chart(fig_month_income, width="stretch", key=f"month_income_{selected_month}")

    st.subheader("本月主要资金流出")
    outflow_top10 = get_top_spending(month_df)

    if outflow_top10.empty:
        st.info("本月没有资金流出")
    else:
        show_cols = [c for c in ["Date", "Description", "Amount", "Category", "SourceFile"] if c in outflow_top10.columns]
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


# ======================
# 页面主体
# ======================
user_id = login_ui()

if st.sidebar.button("测试 Supabase 连接"):
    supabase.table("transactions").select("*").limit(1).execute()
    st.sidebar.success("Supabase 连接成功")

history_df = load_user_transactions(user_id, supabase)
history_for_summary = normalize_history_df(history_df)

st.title("AI记账软件")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "总览",
    "上传账单",
    "月度报告",
    "历史交易",
    "账单管理"
])

# ========== 总览 ==========
with tab1:
    st.subheader("账户总览")

    if history_for_summary.empty:
        st.info("你的云端账户还没有保存交易")
    else:
        show_metric_cards(history_for_summary)
        st.divider()
        st.subheader("历史趋势")
        show_financial_summary(history_for_summary, key_prefix="dashboard")

# ========== 上传账单 ==========
with tab2:
    st.subheader("上传账单")

    uploaded_files = st.file_uploader(
        "上传一个或多个Excel/PDF账单",
        type=["xlsx", "pdf"],
        accept_multiple_files=True,
        key="upload_files"
    )

    if not uploaded_files:
        st.info("请先上传账单，然后再保存到账户")
    else:
        all_dataframes = []

        for uploaded_file in uploaded_files:
            st.write(f"正在读取：{uploaded_file.name}")

            if uploaded_file.name.lower().endswith(".xlsx"):
                temp_df = read_excel_file(uploaded_file)
            elif uploaded_file.name.lower().endswith(".pdf"):
                temp_df = read_pdf_file(uploaded_file, client)
            else:
                continue

            if temp_df is not None and not temp_df.empty:
                temp_df["SourceFile"] = uploaded_file.name
                all_dataframes.append(temp_df)

        if len(all_dataframes) == 0:
            st.warning("没有识别到任何交易记录")
        else:
            df = pd.concat(all_dataframes, ignore_index=True)

            required_columns = {"Date", "Description", "Amount"}
            if not required_columns.issubset(df.columns):
                st.error("账单缺少 Date、Description 或 Amount 列，无法处理")
            else:
                df["Date"] = df["Date"].apply(normalize_date)
                invalid_date_count = int((df["Date"] == "").sum())
                if invalid_date_count:
                    st.warning(
                        f"已跳过 {invalid_date_count} 条日期无效或缺少年份的交易"
                    )
                    df = df[df["Date"] != ""].copy()

                cloud_rules_df = load_user_rules(user_id, supabase)
                local_rules_df = load_rules()

                def classify_transaction(description):
                    cloud_category = classify_by_cloud_rules(description, cloud_rules_df)
                    if cloud_category != "待分类":
                        return cloud_category
                    return classify_by_rules(description, local_rules_df)

                df["Category"] = df["Description"].apply(classify_transaction)
                df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
                df = df.dropna(subset=["Amount"])

                pending_df = df[df["Category"] == "待分类"]

                if len(pending_df) > 0:
                    st.warning(f"发现 {len(pending_df)} 条待分类交易")
                    if st.button("AI处理待分类", key="ai_classify_pending"):
                        with st.spinner("AI正在分类，请稍等..."):
                            for index, row in pending_df.iterrows():
                                description = row["Description"]
                                amount = row["Amount"]
                                category = ai_classify(
                                    description,
                                    amount,
                                    client
                                )
                                df.at[index, "Category"] = category
                                save_rule(description, category)
                                save_cloud_rule(
                                    user_id,
                                    description,
                                    category,
                                    supabase
                                )
                        st.success("AI分类完成，并已写入记忆库")

                st.subheader("分类结果（可手动修改类别）")
                edited_df = st.data_editor(
                    df,
                    width="stretch",
                    num_rows="fixed",
                    column_config={
                        "Category": st.column_config.SelectboxColumn(
                            "Category",
                            options=CATEGORIES,
                            required=True
                        )
                    },
                    key="edited_upload_df"
                )

                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("保存分类到记忆库", key="save_rules_from_edit"):
                        for _, row in edited_df.iterrows():
                            save_rule(row["Description"], row["Category"])
                            save_cloud_rule(
                                user_id,
                                row["Description"],
                                row["Category"],
                                supabase
                            )
                        st.success("已保存分类记忆，下次类似交易会自动识别")

                with col_b:
                    if st.button("保存到账户", key="save_transactions"):
                        saved_count, skipped_count = save_transactions_to_supabase(
                            edited_df,
                            user_id,
                            supabase
                        )
                        st.success(f"保存完成：新增 {saved_count} 条，跳过重复 {skipped_count} 条")

                st.divider()
                show_financial_summary(edited_df, key_prefix="upload")

# ========== 月度报告 ==========
with tab3:
    show_monthly_report(history_for_summary, user_id)

# ========== 历史交易 ==========
with tab4:
    st.subheader("历史交易")

    if history_for_summary.empty:
        st.info("你的云端账户还没有保存交易")
    else:
        st.write(f"云端账户中共有 {len(history_for_summary)} 条交易记录")

        filtered_df = history_for_summary.copy()

        col1, col2 = st.columns(2)

        with col1:
            categories = ["全部"] + sorted(filtered_df["Category"].dropna().unique().tolist())
            selected_category = st.selectbox("按类别筛选", categories, key="history_category_filter")

        with col2:
            if "SourceFile" in filtered_df.columns:
                files = ["全部"] + sorted(filtered_df["SourceFile"].dropna().unique().tolist())
                selected_file = st.selectbox("按账单文件筛选", files, key="history_file_filter")
            else:
                selected_file = "全部"

        if selected_category != "全部":
            filtered_df = filtered_df[filtered_df["Category"] == selected_category]

        if selected_file != "全部" and "SourceFile" in filtered_df.columns:
            filtered_df = filtered_df[filtered_df["SourceFile"] == selected_file]

        st.caption("默认显示最近100条交易")
        st.dataframe(filtered_df.head(100), width="stretch")

# ========== 账单管理 ==========
with tab5:
    st.subheader("账单管理")

    if history_df.empty:
        st.info("你的云端账户还没有保存交易")
    else:
        files = history_df["source_file"].dropna().unique().tolist()

        if len(files) == 0:
            st.info("没有可删除的账单文件")
        else:
            statement_summary = (
                history_df.groupby("source_file")
                .agg(
                    transaction_count=("id", "count"),
                    total_amount=("amount", "sum")
                )
                .reset_index()
                .sort_values("source_file")
            )

            st.subheader("已上传账单")
            st.dataframe(statement_summary, width="stretch")

            selected_file = st.selectbox("选择要删除的账单文件", files, key="delete_statement_file")
            file_count = len(history_df[history_df["source_file"] == selected_file])

            st.warning(f"这个账单包含 {file_count} 条交易，删除后不可恢复")
            confirm_delete = st.checkbox("我确认要删除这个账单", key="confirm_delete_statement")

            if st.button("删除这个账单", key="delete_statement_button"):
                if not confirm_delete:
                    st.error("请先勾选确认删除")
                else:
                    delete_transactions_by_statement(
                        user_id,
                        selected_file,
                        supabase
                    )
                    st.success(f"已删除：{selected_file}")
                    st.rerun()

