import streamlit as st
import pandas as pd
import os
import re
import pdfplumber
import plotly.express as px
from openai import OpenAI
from supabase import create_client

st.set_page_config(page_title="AI记账软件", layout="wide")
st.title("AI记账软件")

RULES_FILE = "merchant_rules.csv"

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

IGNORE_CATEGORIES = ["转账还款", "投资"]


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


user_id = login_ui()


# ======================
# Supabase 交易 / 云端规则
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

    if not merchant or not category:
        return

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


if st.sidebar.button("测试 Supabase 连接"):
    supabase.table("transactions").select("*").limit(1).execute()
    st.sidebar.success("Supabase 连接成功")


# ======================
# 本地记忆库
# ======================

def load_rules():
    if os.path.exists(RULES_FILE):
        return pd.read_csv(RULES_FILE)
    return pd.DataFrame(columns=["merchant", "category"])


def save_rule(merchant, category):
    rules_df = load_rules()
    merchant = str(merchant).upper().strip()

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

    if "WIRE TRANSFER FEE" in desc or "MONTHLY SERVICE FEE" in desc:
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

    if "MCDONALD" in desc or "IN-N-OUT" in desc or "DOMINO" in desc or "PHO HA" in desc or "FIVE GUYS" in desc or "RAISING CANE" in desc:
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

    if "ZELLE PAYMENT" in desc:
        return "转账还款"

    return None


def classify_by_rules(description, rules_df):
    quick = quick_classify(description)
    if quick:
        return quick

    desc = str(description).upper()

    for _, row in rules_df.iterrows():
        merchant = str(row["merchant"]).upper()
        category = row["category"]

        if merchant and merchant in desc:
            return category

    return "待分类"


def classify_by_cloud_rules(description, cloud_rules_df):
    desc = str(description).upper()

    if cloud_rules_df.empty:
        return "待分类"

    for _, row in cloud_rules_df.iterrows():
        merchant = str(row["merchant"]).upper()
        category = row["category"]

        if merchant and merchant in desc:
            return category

    return "待分类"


def ai_classify(description, amount):
    quick = quick_classify(description)
    if quick:
        return quick

    prompt = f"""
你是一个美国银行账单分类助手。

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
# PDF 解析
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


def read_pdf_file(uploaded_file):
    text = extract_pdf_text(uploaded_file)
    statement_type = detect_statement_type(text)

    st.write(f"识别账单类型：{statement_type}")

    if statement_type == "boa_deposit":
        return parse_boa_deposit_pdf(text)

    if statement_type == "boa_credit_card":
        return parse_boa_credit_card_pdf(text)

    if statement_type == "amazon_synchrony":
        return parse_amazon_synchrony_pdf(text)

    if statement_type == "chase_checking":
        return parse_chase_checking_pdf(text)

    return parse_generic_pdf(text)


def read_excel_file(uploaded_file):
    return pd.read_excel(uploaded_file)


# ======================
# 财务统计
# ======================

def show_financial_summary(df, key_prefix="summary"):
    if df is None or df.empty:
        st.info("暂无数据")
        return

    df = df.copy()

    if "Amount" not in df.columns or "Category" not in df.columns:
        st.warning("数据缺少 Amount 或 Category 列，无法统计")
        return

    real_income = df[
        (df["Amount"] > 0)
        &
        (~df["Category"].isin(IGNORE_CATEGORIES))
    ]["Amount"].sum()

    real_expense = abs(
        df[
            (df["Amount"] < 0)
            &
            (~df["Category"].isin(IGNORE_CATEGORIES))
        ]["Amount"].sum()
    )

    transfer_total = abs(df[df["Category"] == "转账还款"]["Amount"].sum())
    investment_total = abs(df[df["Category"] == "投资"]["Amount"].sum())

    st.subheader("财务统计")
    st.write(f"真实收入: ${round(real_income, 2)}")
    st.write(f"真实支出: ${round(real_expense, 2)}")
    st.write(f"转账还款合计: ${round(transfer_total, 2)}")
    st.write(f"投资合计: ${round(investment_total, 2)}")

    expense_df = df[
        (df["Amount"] < 0)
        &
        (~df["Category"].isin(IGNORE_CATEGORIES))
    ].copy()

    expense_df["Amount"] = expense_df["Amount"].abs()

    category_summary = (
        expense_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("真实消费分类汇总")
    st.dataframe(category_summary)

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
            use_container_width=True,
            key=f"{key_prefix}_expense_chart"
        )

    income_df = df[
        (df["Amount"] > 0)
        &
        (~df["Category"].isin(IGNORE_CATEGORIES))
    ].copy()

    income_summary = (
        income_df.groupby("Category")["Amount"]
        .sum()
        .reset_index()
        .sort_values("Amount", ascending=False)
    )

    st.subheader("收入分类汇总")
    st.dataframe(income_summary)

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
            use_container_width=True,
            key=f"{key_prefix}_income_chart"
        )

    cashflow_df = df[
        ~df["Category"].isin(IGNORE_CATEGORIES)
    ].copy()

    cashflow_df["Date"] = pd.to_datetime(cashflow_df["Date"], errors="coerce")
    cashflow_df = cashflow_df.dropna(subset=["Date"])

    if cashflow_df.empty:
        return

    cashflow_df["Month"] = cashflow_df["Date"].dt.strftime("%Y-%m")

    monthly_cashflow = (
        cashflow_df.groupby("Month")
        .agg(
            Income=("Amount", lambda x: x[x > 0].sum()),
            Expense=("Amount", lambda x: abs(x[x < 0].sum()))
        )
        .reset_index()
        .sort_values("Month")
    )

    monthly_cashflow["NetCashFlow"] = monthly_cashflow["Income"] - monthly_cashflow["Expense"]

    st.subheader("月度现金流")
    st.dataframe(monthly_cashflow.round(2))

    if not monthly_cashflow.empty:
        fig_cashflow = px.line(
            monthly_cashflow,
            x="Month",
            y=["Income", "Expense", "NetCashFlow"],
            markers=True,
            title="月度现金流趋势"
        )
        st.plotly_chart(
            fig_cashflow,
            use_container_width=True,
            key=f"{key_prefix}_cashflow_chart"
        )


def normalize_history_df(history_df):
    if history_df.empty:
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


# ======================
# Dashboard
# ======================

history_df = load_user_transactions(user_id)
history_for_summary = normalize_history_df(history_df)

st.subheader("账户总览")

if history_for_summary.empty:
    st.info("你的云端账户还没有保存交易")
else:
    dashboard_df = history_for_summary.copy()

    real_dashboard_df = dashboard_df[
        ~dashboard_df["Category"].isin(IGNORE_CATEGORIES)
    ]

    total_income = real_dashboard_df[
        real_dashboard_df["Amount"] > 0
    ]["Amount"].sum()

    total_expense = abs(
        real_dashboard_df[
            real_dashboard_df["Amount"] < 0
        ]["Amount"].sum()
    )

    net_cashflow = total_income - total_expense
    total_transactions = len(dashboard_df)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("真实收入", f"${total_income:,.2f}")
    col2.metric("真实支出", f"${total_expense:,.2f}")
    col3.metric("净现金流", f"${net_cashflow:,.2f}")
    col4.metric("交易数量", total_transactions)


# ======================
# 上传账单
# ======================

uploaded_files = st.file_uploader(
    "上传一个或多个Excel/PDF账单",
    type=["xlsx", "pdf"],
    accept_multiple_files=True
)

if uploaded_files:
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

        st.subheader("合并后的账单内容")
        st.dataframe(df)

        cloud_rules_df = load_cloud_rules(user_id)
        local_rules_df = load_rules()

        def classify_transaction(description):
            cloud_category = classify_by_cloud_rules(
                description,
                cloud_rules_df
            )

            if cloud_category != "待分类":
                return cloud_category

            return classify_by_rules(
                description,
                local_rules_df
            )

        df["Category"] = df["Description"].apply(classify_transaction)

        st.subheader("分类结果")
        st.dataframe(df)

        pending_df = df[df["Category"] == "待分类"]

        if len(pending_df) > 0:
            st.warning(f"发现 {len(pending_df)} 条待分类交易")

            if st.button("AI处理待分类"):
                with st.spinner("AI正在分类，请稍等..."):
                    for index, row in pending_df.iterrows():
                        description = row["Description"]
                        amount = row["Amount"]

                        category = ai_classify(description, amount)

                        df.at[index, "Category"] = category
                        save_rule(description, category)
                        save_cloud_rule(user_id, description, category)

                st.success("AI分类完成，并已写入记忆库")
                st.rerun()

        if st.button("保存到账户"):
            saved_count, skipped_count = save_transactions_to_supabase(
                df,
                user_id
            )

            st.success(
                f"保存完成：新增 {saved_count} 条，跳过重复 {skipped_count} 条"
            )

        show_financial_summary(df, key_prefix="upload")
else:
    st.info("请先上传账单，然后再保存到账户")


# ======================
# 删除已上传账单
# ======================

st.subheader("删除已上传账单")

history_df = load_user_transactions(user_id)

if history_df.empty:
    st.info("你的云端账户还没有保存交易")
else:
    files = (
        history_df["source_file"]
        .dropna()
        .unique()
        .tolist()
    )

    if len(files) == 0:
        st.info("没有可删除的账单文件")
    else:
        selected_file = st.selectbox(
            "选择要删除的账单文件",
            files
        )

        file_count = len(
            history_df[history_df["source_file"] == selected_file]
        )

        st.warning(
            f"这个账单包含 {file_count} 条交易，删除后不可恢复"
        )

        confirm_delete = st.checkbox("我确认要删除这个账单")

        if st.button("删除这个账单"):
            if not confirm_delete:
                st.error("请先勾选确认删除")
            else:
                supabase.table("transactions") \
                    .delete() \
                    .eq("user_id", str(user_id)) \
                    .eq("source_file", selected_file) \
                    .execute()

                st.success(f"已删除：{selected_file}")
                st.rerun()


# ======================
# 云端历史交易
# ======================

st.subheader("云端历史交易记录")

history_df = load_user_transactions(user_id)
history_for_summary = normalize_history_df(history_df)

if history_for_summary.empty:
    st.info("你的云端账户还没有保存交易")
else:
    st.write(f"云端账户中共有 {len(history_for_summary)} 条交易记录")
    st.dataframe(history_for_summary)

    st.subheader("云端历史总览")
    show_financial_summary(history_for_summary, key_prefix="history")

