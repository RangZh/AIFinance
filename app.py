import os
import re
import json
import pdfplumber
import pandas as pd
import plotly.express as px
import streamlit as st
from openai import OpenAI
from supabase import create_client

# ======================
# 基础设置
# ======================
st.set_page_config(page_title="AI记账软件", layout="wide")

RULES_FILE = "merchant_rules.csv"

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

CATEGORIES = [
    "餐饮", "超市日用品", "交通加油", "住房", "水电网", "保险",
    "医疗", "教育", "电商收入", "电商物流", "电商进货",
    "摄影业务", "软件订阅", "转账还款", "退款", "投资",
    "房贷", "银行手续费", "利息收入", "税费", "其他"
]

# 不计入真实消费/真实收入的类别
IGNORE_CATEGORIES = {"转账还款", "投资"}

# 真实收入类别：收入汇总只看这些类别，避免餐饮/医疗等退款跑进收入
INCOME_CATEGORIES = {
    "电商收入",
    "摄影业务",
    "利息收入",
    "退款"
}

# 真实消费类别：消费汇总只看这些类别
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
# Supabase：交易与云端规则
# ======================
def make_cloud_unique_key(row, user_id):
    return (
        str(user_id)
        + "|"
        + str(row["Date"])
        + "|"
        + str(row["Description"])
        + "|"
        + str(float(row["Amount"]))
        + "|"
        + str(row.get("SourceFile", ""))
    )


def save_transactions_to_supabase(df, user_id):
    saved_count = 0
    skipped_count = 0

    if df is None or df.empty:
        return saved_count, skipped_count

    for _, row in df.iterrows():
        date_value = str(row["Date"])
        description = str(row["Description"])
        amount = float(row["Amount"])
        category = str(row["Category"])
        source_file = str(row.get("SourceFile", ""))

        date_parsed = pd.to_datetime(date_value, errors="coerce")
        month = date_parsed.strftime("%Y-%m") if pd.notna(date_parsed) else ""
        unique_key = make_cloud_unique_key(row, user_id)

        try:
            existing = (
                supabase.table("transactions")
                .select("id")
                .eq("user_id", str(user_id))
                .eq("unique_key", unique_key)
                .execute()
            )

            if existing.data:
                skipped_count += 1
                continue

            record = {
                "user_id": str(user_id),
                "date": date_value,
                "description": description,
                "amount": amount,
                "category": category,
                "source_file": source_file,
                "month": month,
                "unique_key": unique_key
            }

            supabase.table("transactions").insert(record).execute()
            saved_count += 1

        except Exception as e:
            skipped_count += 1
            st.warning(f"有一条交易保存失败，已跳过：{description} / {amount} / {e}")

    return saved_count, skipped_count


def load_user_transactions(user_id):
    result = (
        supabase.table("transactions")
        .select("*")
        .eq("user_id", str(user_id))
        .order("date", desc=True)
        .execute()
    )
    return pd.DataFrame(result.data)


def load_cloud_rules(user_id):
    result = (
        supabase.table("merchant_rules")
        .select("*")
        .eq("user_id", str(user_id))
        .execute()
    )

    if result.data:
        return pd.DataFrame(result.data)

    return pd.DataFrame(columns=["merchant", "category"])


def save_cloud_rule(user_id, merchant, category):
    merchant = str(merchant).upper().strip()
    category = str(category).strip()

    if not merchant or not category or category == "待分类":
        return

    try:
        existing = (
            supabase.table("merchant_rules")
            .select("id")
            .eq("user_id", str(user_id))
            .eq("merchant", merchant)
            .execute()
        )

        if existing.data:
            return

        supabase.table("merchant_rules").insert({
            "user_id": str(user_id),
            "merchant": merchant,
            "category": category
        }).execute()
    except Exception as e:
        st.warning(f"云端记忆库保存失败：{e}")


def delete_statement_by_file(user_id, source_file):
    supabase.table("transactions") \
        .delete() \
        .eq("user_id", str(user_id)) \
        .eq("source_file", source_file) \
        .execute()


# ======================
# 本地记忆库
# ======================
def load_rules():
    if os.path.exists(RULES_FILE):
        return pd.read_csv(RULES_FILE)
    return pd.DataFrame(columns=["merchant", "category"])


def save_rule(merchant, category):
    merchant = str(merchant).upper().strip()
    category = str(category).strip()

    if not merchant or not category or category == "待分类":
        return

    rules_df = load_rules()

    exists = rules_df["merchant"].astype(str).str.upper().eq(merchant).any()

    if not exists:
        new_row = pd.DataFrame([{
            "merchant": merchant,
            "category": category
        }])
        rules_df = pd.concat([rules_df, new_row], ignore_index=True)
        rules_df.to_csv(RULES_FILE, index=False)


# ======================
# 分类
# ======================
def quick_classify(description):
    desc = str(description).upper()

    if "INTEREST EARNED" in desc or "INTEREST CREDIT" in desc or "SAVINGS INTEREST" in desc:
        return "利息收入"

    if "FEDERAL WITHHOLDING" in desc or "FRANCHISE TAX" in desc:
        return "税费"

    if "WIRE TRANSFER FEE" in desc or "MONTHLY SERVICE FEE" in desc or "BANK CHARGE" in desc:
        return "银行手续费"

    if "ETSY" in desc or "EBAY" in desc or "TIKTOK" in desc:
        return "电商收入"

    if "DOORDASH" in desc or "WALMART INC DES:PAYMENT" in desc or "WALMART INC DES:TIPS" in desc:
        return "电商收入"

    if "COSTCO" in desc or "WALMART" in desc or "TARGET" in desc or "MURRIETA GROCERY" in desc:
        return "超市日用品"

    if "SHELL" in desc or "CHEVRON" in desc or "ARCO" in desc or "COSTCO GAS" in desc:
        return "交通加油"

    if "STATE FARM" in desc or "GEICO" in desc:
        return "保险"

    if "ADOBE" in desc or "OPENAI" in desc or "CHATGPT" in desc or "CAPCUT" in desc:
        return "软件订阅"

    if "USPS" in desc or "UPS" in desc or "FEDEX" in desc or "PIRATE SHIP" in desc:
        return "电商物流"

    if "SCHWAB" in desc or "BETTERMENT" in desc or "AMERICAN FUNDS" in desc or "FIDELITY" in desc or "VANGUARD" in desc:
        return "投资"

    if "PACIFIC LANDING" in desc:
        return "房贷"

    if "SO CAL EDISON" in desc or "SOCALGAS" in desc or "FRONTIER" in desc:
        return "水电网"

    if "KAISER" in desc:
        return "医疗"

    if "MCDONALD" in desc or "IN-N-OUT" in desc or "DOMINO" in desc or "PHO HA" in desc or "FIVE GUYS" in desc or "RAISING CANE" in desc or "STARBUCKS" in desc:
        return "餐饮"

    if "ONLINE BANKING TRANSFER" in desc:
        return "转账还款"

    if "ONLINE TRANSFER" in desc:
        return "转账还款"

    if "MOBILE BANKING PAYMENT" in desc:
        return "转账还款"

    if "PAYMENT FROM" in desc:
        return "转账还款"

    if "WIRE TYPE:WIRE IN" in desc or "WIRE TYPE:WIRE OUT" in desc or "WIRE TYPE:INTL IN" in desc or "WIRE TYPE:BOOK IN" in desc:
        return "转账还款"

    if "WIRE OUT" in desc or "WIRE IN" in desc:
        return "转账还款"

    if "ZELLE PAYMENT" in desc:
        return "转账还款"

    return None


def classify_by_rules(description, rules_df):
    quick = quick_classify(description)
    if quick:
        return quick

    desc = str(description).upper()

    for _, row in rules_df.iterrows():
        merchant = str(row.get("merchant", "")).upper()
        category = row.get("category", "待分类")

        if merchant and merchant in desc:
            return category

    return "待分类"


def classify_by_cloud_rules(description, cloud_rules_df):
    quick = quick_classify(description)
    if quick:
        return quick

    desc = str(description).upper()

    if cloud_rules_df.empty:
        return "待分类"

    for _, row in cloud_rules_df.iterrows():
        merchant = str(row.get("merchant", "")).upper()
        category = row.get("category", "待分类")

        if merchant and merchant in desc:
            return category

    return "待分类"


def ai_classify(description, amount):
    quick = quick_classify(description)
    if quick:
        return quick

    prompt = f"""
你是一个银行账单分类助手。

请根据交易描述和金额判断类别。

只能从下面类别中选择一个：
{CATEGORIES}

交易描述：{description}
金额：{amount}

只返回类别名称，不要解释。
"""

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )

    result = response.output_text.strip()

    if result not in CATEGORIES:
        return "其他"

    return result


# ======================
# PDF / Excel 解析
# ======================
def extract_pdf_text(uploaded_file):
    text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def detect_statement_type(text):
    upper_text = text.upper()

    if "SYNCHRONY BANK" in upper_text or "AMAZON.SYF.COM" in upper_text:
        return "amazon_synchrony"

    if "VISA SIGNATURE" in upper_text and "BANK OF AMERICA" in upper_text:
        return "boa_credit_card"

    if "BANK OF AMERICA" in upper_text and (
        "ADVANTAGE SAVINGS" in upper_text
        or "SAFEBALANCE BANKING" in upper_text
        or "DEPOSITS AND OTHER ADDITIONS" in upper_text
        or "ACCOUNT ACTIVITY" in upper_text
        or "POSTING DATE" in upper_text
        or "PRINT TRANSACTION DETAILS" in upper_text
    ):
        return "boa_deposit"

    if "CHASE TOTAL CHECKING" in upper_text or "JPMORGAN CHASE BANK" in upper_text:
        return "chase_checking"

    return "unknown"


def normalize_date(date_text):
    date_text = str(date_text).strip()

    if re.match(r"^\d{2}/\d{2}/\d{2}$", date_text):
        parsed = pd.to_datetime(date_text, format="%m/%d/%y", errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%m/%d/%Y")

    if re.match(r"^\d{2}/\d{2}/\d{4}$", date_text):
        return date_text

    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_text):
        parsed = pd.to_datetime(date_text, errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%m/%d/%Y")

    if re.match(r"^\d{2}/\d{2}$", date_text):
        return date_text + "/2026"

    return date_text


def parse_amount(amount_text):
    amount_text = str(amount_text)
    amount_text = amount_text.replace("$", "")
    amount_text = amount_text.replace(",", "")
    amount_text = amount_text.replace("−", "-")
    return float(amount_text)


def parse_boa_deposit_pdf(text):
    transactions = []
    lines = text.splitlines()
    section = None
    current = None

    def flush_current():
        if current:
            transactions.append(current.copy())

    for line in lines:
        raw = line.strip()
        upper = raw.upper()

        if not raw:
            continue

        if "DEPOSITS AND OTHER ADDITIONS" in upper:
            flush_current()
            current = None
            section = "deposit"
            continue

        if "WITHDRAWALS AND OTHER SUBTRACTIONS" in upper:
            flush_current()
            current = None
            section = "withdrawal"
            continue

        if upper.startswith("TOTAL DEPOSITS") or upper.startswith("TOTAL OTHER SUBTRACTIONS") or upper.startswith("TOTAL ATM"):
            flush_current()
            current = None
            section = None
            continue

        if section not in ["deposit", "withdrawal"]:
            continue

        match = re.match(
            r"^(\d{2}/\d{2}/\d{2})\s+(.+?)\s+(-?\$?[\d,]+\.\d{2})$",
            raw
        )

        if match:
            flush_current()
            date, desc, amount = match.groups()
            amount = parse_amount(amount)

            if section == "withdrawal" and amount > 0:
                amount = -amount

            current = {
                "Date": normalize_date(date),
                "Description": desc.strip(),
                "Amount": amount
            }
        else:
            if current and not upper.startswith("DATE DESCRIPTION AMOUNT"):
                current["Description"] += " " + raw

    flush_current()
    return pd.DataFrame(transactions)


def parse_boa_credit_card_pdf(text):
    transactions = []
    lines = text.splitlines()
    section = None

    for line in lines:
        raw = line.strip()
        upper = raw.upper()

        if not raw:
            continue

        if "PAYMENTS AND OTHER CREDITS" in upper:
            section = "credit"
            continue

        if "PURCHASES AND ADJUSTMENTS" in upper:
            section = "purchase"
            continue

        if "TOTAL PAYMENTS AND OTHER CREDITS" in upper or "TOTAL PURCHASES AND ADJUSTMENTS" in upper:
            section = None
            continue

        if section not in ["credit", "purchase"]:
            continue

        match = re.match(
            r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+\d{4}\s+\d{4}\s+(-?[\d,]+\.\d{2})$",
            raw
        )

        if match:
            trans_date, post_date, desc, amount = match.groups()
            amount = parse_amount(amount)

            if section == "credit" and amount > 0:
                amount = -amount

            transactions.append({
                "Date": normalize_date(trans_date),
                "Description": desc.strip(),
                "Amount": amount
            })

    return pd.DataFrame(transactions)


def parse_amazon_synchrony_pdf(text):
    transactions = []
    lines = text.splitlines()
    section = None

    for line in lines:
        raw = line.strip()
        upper = raw.upper()

        if not raw:
            continue

        if "PAYMENTS" in upper and "TOTAL" not in upper:
            section = "credit"
            continue

        if "OTHER CREDITS" in upper and "TOTAL" not in upper:
            section = "credit"
            continue

        if "PURCHASES AND OTHER DEBITS" in upper:
            section = "purchase"
            continue

        if section not in ["credit", "purchase"]:
            continue

        match = re.match(
            r"^(\d{2}/\d{2})\s+\S+\s+(.+?)\s+\$?(-?[\d,]+\.\d{2})$",
            raw
        )

        if match:
            date, desc, amount = match.groups()
            amount = parse_amount(amount)

            if section == "credit" and amount > 0:
                amount = -amount

            transactions.append({
                "Date": normalize_date(date),
                "Description": desc.strip(),
                "Amount": amount
            })

    return pd.DataFrame(transactions)


def parse_chase_checking_pdf(text):
    transactions = []
    lines = text.splitlines()
    in_detail = False

    for line in lines:
        raw = line.strip()
        upper = raw.upper()

        if "TRANSACTION DETAIL" in upper:
            in_detail = True
            continue

        if "DAILY ENDING BALANCE" in upper or "SERVICE FEE SUMMARY" in upper:
            in_detail = False
            continue

        if not in_detail:
            continue

        match = re.match(
            r"^(\d{2}/\d{2})\s+(.+?)\s+(-?[\d,]+\.\d{2})\s+[\d,]+\.\d{2}$",
            raw
        )

        if match:
            date, desc, amount = match.groups()
            amount = parse_amount(amount)

            transactions.append({
                "Date": normalize_date(date),
                "Description": desc.strip(),
                "Amount": amount
            })

    return pd.DataFrame(transactions)


def parse_generic_pdf(text):
    pattern = r"(\d{2}/\d{2}/\d{4})(.*?)([-]?\$?[\d,]+\.\d{2})"
    matches = re.findall(pattern, text, re.DOTALL)
    transactions = []

    for date, desc, amount in matches:
        desc = desc.replace("\n", " ").strip()

        if "VIEW: TODAY" in desc.upper():
            continue

        amount = parse_amount(amount)

        transactions.append({
            "Date": normalize_date(date),
            "Description": desc,
            "Amount": amount
        })

    return pd.DataFrame(transactions)


def parse_pdf_with_ai(text):
    prompt = f"""
你是银行账单解析助手。

从下面账单中提取所有交易记录。

返回JSON数组格式：
[
  {{"Date":"2026-01-01", "Description":"STARBUCKS", "Amount":-8.5}}
]

要求：
1. 只返回JSON
2. 不要解释
3. 金额支出为负数
4. 金额收入为正数
5. 如果原文没有年份，请尽量根据账单上下文补全年份

账单内容：
{text[:30000]}
"""

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt
    )

    try:
        result = response.output_text.strip()
        result = result.replace("```json", "").replace("```", "").strip()
        transactions = json.loads(result)
        df = pd.DataFrame(transactions)

        required_cols = {"Date", "Description", "Amount"}
        if not required_cols.issubset(set(df.columns)):
            st.error("AI解析结果缺少 Date / Description / Amount 列")
            return pd.DataFrame()

        df["Date"] = df["Date"].apply(normalize_date)
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
        df = df.dropna(subset=["Amount"])
        return df

    except Exception as e:
        st.error(f"AI解析失败: {e}")
        return pd.DataFrame()


def read_pdf_file(uploaded_file):
    text = extract_pdf_text(uploaded_file)
    statement_type = detect_statement_type(text)

    st.write(f"识别账单类型：{statement_type}")

    if statement_type == "boa_deposit":
        result = parse_boa_deposit_pdf(text)
        if not result.empty:
            return result
        st.info("未匹配标准银行月结单格式，正在使用AI识别...")
        return parse_pdf_with_ai(text)

    if statement_type == "boa_credit_card":
        result = parse_boa_credit_card_pdf(text)
        if not result.empty:
            return result
        st.info("信用卡规则解析失败，正在使用AI识别...")
        return parse_pdf_with_ai(text)

    if statement_type == "amazon_synchrony":
        result = parse_amazon_synchrony_pdf(text)
        if not result.empty:
            return result
        return parse_pdf_with_ai(text)

    if statement_type == "chase_checking":
        result = parse_chase_checking_pdf(text)
        if not result.empty:
            return result
        return parse_pdf_with_ai(text)

    result = parse_generic_pdf(text)
    if not result.empty:
        return result

    st.info("未匹配标准银行月结单格式，正在使用AI识别...")
    return parse_pdf_with_ai(text)


def read_excel_file(uploaded_file):
    return pd.read_excel(uploaded_file)


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
            "source_file": "SourceFile"
        }
    )

    if "Amount" in df.columns:
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")

    return df


def prepare_report_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    report_df = df.copy()
    report_df["Date"] = pd.to_datetime(report_df["Date"], errors="coerce")
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

    expense_df = df[df["Category"].isin(EXPENSE_CATEGORIES)].copy()
    expense_df["Amount"] = expense_df["Amount"].abs()

    category_summary = (
        expense_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("消费分类汇总")
    st.dataframe(category_summary, use_container_width=True)

    st.subheader("消费分类明细")
    for category in category_summary["Category"]:
        category_detail = expense_df[expense_df["Category"] == category].copy()
        category_total = category_detail["Amount"].sum()
        with st.expander(f"{category} - ${category_total:,.2f}"):
            show_cols = [c for c in ["Date", "Description", "Amount", "SourceFile"] if c in category_detail.columns]
            st.dataframe(category_detail[show_cols], use_container_width=True)

    if not category_summary.empty:
        fig_expense = px.bar(
            category_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title="真实消费分类图"
        )
        st.plotly_chart(fig_expense, use_container_width=True, key=f"{key_prefix}_expense_chart")

    income_df = df[df["Category"].isin(INCOME_CATEGORIES)].copy()
    income_df["Amount"] = income_df["Amount"].abs()

    income_summary = (
        income_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("收入分类汇总")
    st.dataframe(income_summary, use_container_width=True)

    if not income_summary.empty:
        fig_income = px.bar(
            income_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title="收入分类图"
        )
        st.plotly_chart(fig_income, use_container_width=True, key=f"{key_prefix}_income_chart")

    report_df = prepare_report_df(df)
    if report_df.empty:
        return

    monthly_summary = []
    for month, month_df in report_df.groupby("Month"):
        month_income, month_expense, month_net, _ = calculate_summary(month_df)
        monthly_summary.append({
            "Month": month,
            "Income": month_income,
            "Expense": month_expense,
            "NetCashFlow": month_net
        })

    monthly_cashflow = pd.DataFrame(monthly_summary).sort_values("Month")

    st.subheader("月度现金流")
    st.dataframe(monthly_cashflow.round(2), use_container_width=True)

    if not monthly_cashflow.empty:
        fig_cashflow = px.line(
            monthly_cashflow,
            x="Month",
            y=["Income", "Expense", "NetCashFlow"],
            markers=True,
            title="月度现金流趋势"
        )
        st.plotly_chart(fig_cashflow, use_container_width=True, key=f"{key_prefix}_cashflow_chart")


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

    expense_df = month_df[month_df["Category"].isin(EXPENSE_CATEGORIES)].copy()
    expense_df["Amount"] = expense_df["Amount"].abs()

    expense_summary = (
        expense_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("本月支出分类")
    if expense_summary.empty:
        st.info("本月没有真实支出记录")
    else:
        st.dataframe(expense_summary, use_container_width=True)

        for category in expense_summary["Category"]:
            detail = expense_df[expense_df["Category"] == category].copy()
            total = detail["Amount"].sum()
            with st.expander(f"{category} - ${total:,.2f}"):
                show_cols = [c for c in ["Date", "Description", "Amount", "SourceFile"] if c in detail.columns]
                st.dataframe(detail[show_cols], use_container_width=True)

        fig_month_expense = px.bar(
            expense_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title=f"{selected_month} 支出分类"
        )
        st.plotly_chart(fig_month_expense, use_container_width=True, key=f"month_expense_{selected_month}")

    income_df = month_df[month_df["Category"].isin(INCOME_CATEGORIES)].copy()
    income_df["Amount"] = income_df["Amount"].abs()

    income_summary = (
        income_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("本月收入分类")
    if income_summary.empty:
        st.info("本月没有收入记录")
    else:
        st.dataframe(income_summary, use_container_width=True)

        fig_month_income = px.bar(
            income_summary,
            x="Amount",
            y="Category",
            orientation="h",
            title=f"{selected_month} 收入分类"
        )
        st.plotly_chart(fig_month_income, use_container_width=True, key=f"month_income_{selected_month}")

    st.subheader("本月主要资金流出")
    outflow_df = month_df[month_df["Amount"] < 0].copy()
    outflow_df["Amount"] = outflow_df["Amount"].abs()
    outflow_top10 = outflow_df.sort_values("Amount", ascending=False).head(10)

    if outflow_top10.empty:
        st.info("本月没有资金流出")
    else:
        show_cols = [c for c in ["Date", "Description", "Amount", "Category", "SourceFile"] if c in outflow_top10.columns]
        st.dataframe(outflow_top10[show_cols], use_container_width=True)

    if st.button("生成AI财务分析", key=f"ai_report_{selected_month}"):
        with st.spinner("AI正在分析本月财务..."):
            expense_summary_text = expense_summary.to_string(index=False) if not expense_summary.empty else "无"
            income_summary_text = income_summary.to_string(index=False) if not income_summary.empty else "无"
            outflow_text = outflow_top10[["Description", "Amount", "Category"]].to_string(index=False) if not outflow_top10.empty else "无"

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

            st.subheader("🤖 AI财务分析")
            st.write(response.output_text)


# ======================
# 页面主体
# ======================
user_id = login_ui()

if st.sidebar.button("测试 Supabase 连接"):
    supabase.table("transactions").select("*").limit(1).execute()
    st.sidebar.success("Supabase 连接成功")

history_df = load_user_transactions(user_id)
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
                temp_df = read_pdf_file(uploaded_file)
            else:
                continue

            if temp_df is not None and not temp_df.empty:
                temp_df["SourceFile"] = uploaded_file.name
                all_dataframes.append(temp_df)

        if len(all_dataframes) == 0:
            st.warning("没有识别到任何交易记录")
        else:
            df = pd.concat(all_dataframes, ignore_index=True)

            if "Description" not in df.columns or "Amount" not in df.columns:
                st.error("账单缺少 Description 或 Amount 列，无法处理")
            else:
                cloud_rules_df = load_cloud_rules(user_id)
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
                                category = ai_classify(description, amount)
                                df.at[index, "Category"] = category
                                save_rule(description, category)
                                save_cloud_rule(user_id, description, category)
                        st.success("AI分类完成，并已写入记忆库")

                st.subheader("分类结果（可手动修改类别）")
                edited_df = st.data_editor(
                    df,
                    use_container_width=True,
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
                            save_cloud_rule(user_id, row["Description"], row["Category"])
                        st.success("已保存分类记忆，下次类似交易会自动识别")

                with col_b:
                    if st.button("保存到账户", key="save_transactions"):
                        saved_count, skipped_count = save_transactions_to_supabase(edited_df, user_id)
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
        st.dataframe(filtered_df.head(100), use_container_width=True)

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
            st.dataframe(statement_summary, use_container_width=True)

            selected_file = st.selectbox("选择要删除的账单文件", files, key="delete_statement_file")
            file_count = len(history_df[history_df["source_file"] == selected_file])

            st.warning(f"这个账单包含 {file_count} 条交易，删除后不可恢复")
            confirm_delete = st.checkbox("我确认要删除这个账单", key="confirm_delete_statement")

            if st.button("删除这个账单", key="delete_statement_button"):
                if not confirm_delete:
                    st.error("请先勾选确认删除")
                else:
                    delete_statement_by_file(user_id, selected_file)
                    st.success(f"已删除：{selected_file}")
                    st.rerun()

