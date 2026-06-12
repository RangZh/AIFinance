import pandas as pd


INCOME_CATEGORIES = {
    "电商收入",
    "摄影业务",
    "利息收入",
    "退款"
}

EXPENSE_CATEGORIES = {
    "餐饮",
    "超市日用品",
    "交通加油",
    "住房",
    "水电网",
    "保险",
    "医疗",
    "教育",
    "电商物流",
    "电商进货",
    "软件订阅",
    "房贷",
    "银行手续费",
    "税费",
    "其他"
}


def prepare_report_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    report_df = df.copy()
    report_df["Date"] = pd.to_datetime(
        report_df["Date"],
        format="%Y-%m-%d",
        errors="coerce"
    )
    report_df = report_df.dropna(subset=["Date"])
    report_df["Month"] = report_df["Date"].dt.strftime("%Y-%m")
    return report_df


def calculate_summary(df):
    if df is None or df.empty:
        return 0.0, 0.0, 0.0, 0

    real_income_df = df[df["Category"].isin(INCOME_CATEGORIES)].copy()
    real_expense_df = df[df["Category"].isin(EXPENSE_CATEGORIES)].copy()

    income = real_income_df["Amount"].abs().sum()
    expense = real_expense_df["Amount"].abs().sum()
    net = income - expense
    count = len(df)

    return income, expense, net, count


def aggregate_expenses(df):
    expense_df = df[df["Category"].isin(EXPENSE_CATEGORIES)].copy()
    expense_df["Amount"] = expense_df["Amount"].abs()

    expense_summary = (
        expense_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )
    return expense_df, expense_summary


def aggregate_income(df):
    income_df = df[df["Category"].isin(INCOME_CATEGORIES)].copy()
    income_df["Amount"] = income_df["Amount"].abs()

    income_summary = (
        income_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )
    return income_df, income_summary


def build_monthly_cashflow(df):
    report_df = prepare_report_df(df)
    if report_df.empty:
        return pd.DataFrame()

    monthly_summary = []
    for month, month_df in report_df.groupby("Month"):
        month_income, month_expense, month_net, _ = calculate_summary(month_df)
        monthly_summary.append({
            "Month": month,
            "Income": month_income,
            "Expense": month_expense,
            "NetCashFlow": month_net
        })

    return pd.DataFrame(monthly_summary).sort_values("Month")


def get_top_spending(df, limit=10):
    outflow_df = df[df["Amount"] < 0].copy()
    outflow_df["Amount"] = outflow_df["Amount"].abs()
    return outflow_df.sort_values("Amount", ascending=False).head(limit)


def generate_ai_analysis(
    client,
    selected_month,
    income,
    expense,
    net,
    income_summary,
    expense_summary,
    outflow_top10,
):
    expense_summary_text = (
        expense_summary.to_string(index=False)
        if not expense_summary.empty
        else "无"
    )
    income_summary_text = (
        income_summary.to_string(index=False)
        if not income_summary.empty
        else "无"
    )
    outflow_text = (
        outflow_top10[["Description", "Amount", "Category"]].to_string(
            index=False
        )
        if not outflow_top10.empty
        else "无"
    )

    prompt = f"""
你是一位专业但务实的个人财务分析助手。

请根据以下月度财务数据，生成一份通俗易懂的中文财务分析。

月份：{selected_month}
真实收入：{income}
真实支出：{expense}
净现金流：{net}

收入分类：
{income_summary_text}

真实支出分类：
{expense_summary_text}

主要资金流出：
{outflow_text}

要求：
1. 严格根据实际账单分析，不要编造。
2. 如果数据不足，不要给出长期投资建议。
3. 重点分析收入来源、主要支出、异常资金流出、资金流动特点。
4. 禁止输出应急基金建议、提前还房贷建议、投资建议，除非账单明确显示相关信息。
5. 控制在300字以内。
"""

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )
    return response.output_text
