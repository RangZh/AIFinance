import json
import re

import pandas as pd
import pdfplumber
import streamlit as st

from transaction_model import infer_statement_year, normalize_date


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


def parse_amount(amount_text):
    amount_text = str(amount_text)
    amount_text = amount_text.replace("$", "")
    amount_text = amount_text.replace(",", "")
    amount_text = amount_text.replace("−", "-")
    return float(amount_text)


def parse_boa_deposit_pdf(text, default_year=None):
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

        if (
            upper.startswith("TOTAL DEPOSITS")
            or upper.startswith("TOTAL OTHER SUBTRACTIONS")
            or upper.startswith("TOTAL ATM")
        ):
            flush_current()
            current = None
            section = None
            continue

        if section not in ["deposit", "withdrawal"]:
            continue

        match = re.match(
            r"^(\d{2}/\d{2}/\d{2})\s+(.+?)\s+(-?\$?[\d,]+\.\d{2})$",
            raw,
        )

        if match:
            flush_current()
            date, desc, amount = match.groups()
            amount = parse_amount(amount)

            if section == "withdrawal" and amount > 0:
                amount = -amount

            current = {
                "Date": normalize_date(date, default_year),
                "Description": desc.strip(),
                "Amount": amount,
            }
        elif current and not upper.startswith("DATE DESCRIPTION AMOUNT"):
            current["Description"] += " " + raw

    flush_current()
    return pd.DataFrame(transactions)


def parse_boa_credit_card_pdf(text, default_year=None):
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

        if (
            "TOTAL PAYMENTS AND OTHER CREDITS" in upper
            or "TOTAL PURCHASES AND ADJUSTMENTS" in upper
        ):
            section = None
            continue

        if section not in ["credit", "purchase"]:
            continue

        match = re.match(
            r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+\d{4}\s+\d{4}\s+(-?[\d,]+\.\d{2})$",
            raw,
        )

        if match:
            trans_date, _post_date, desc, amount = match.groups()
            amount = parse_amount(amount)
            amount = abs(amount) if section == "credit" else -abs(amount)

            transactions.append({
                "Date": normalize_date(trans_date, default_year),
                "Description": desc.strip(),
                "Amount": amount,
            })

    return pd.DataFrame(transactions)


def parse_amazon_synchrony_pdf(text, default_year=None):
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
            raw,
        )

        if match:
            date, desc, amount = match.groups()
            amount = parse_amount(amount)
            amount = abs(amount) if section == "credit" else -abs(amount)

            transactions.append({
                "Date": normalize_date(date, default_year),
                "Description": desc.strip(),
                "Amount": amount,
            })

    return pd.DataFrame(transactions)


def parse_chase_checking_pdf(text, default_year=None):
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
            raw,
        )

        if match:
            date, desc, amount = match.groups()
            transactions.append({
                "Date": normalize_date(date, default_year),
                "Description": desc.strip(),
                "Amount": parse_amount(amount),
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

        transactions.append({
            "Date": normalize_date(date),
            "Description": desc,
            "Amount": parse_amount(amount),
        })

    return pd.DataFrame(transactions)


def parse_pdf_with_ai(text, client, default_year=None):
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

    response = client.responses.create(model="gpt-5-mini", input=prompt)

    try:
        result = response.output_text.strip()
        result = result.replace("```json", "").replace("```", "").strip()
        transactions = json.loads(result)
        df = pd.DataFrame(transactions)

        required_cols = {"Date", "Description", "Amount"}
        if not required_cols.issubset(set(df.columns)):
            st.error("AI解析结果缺少 Date / Description / Amount 列")
            return pd.DataFrame()

        df["Date"] = df["Date"].apply(
            lambda value: normalize_date(value, default_year)
        )
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
        return df[(df["Date"] != "") & df["Amount"].notna()]

    except Exception as e:
        st.error(f"AI解析失败: {e}")
        return pd.DataFrame()


def read_pdf_file(uploaded_file, client):
    text = extract_pdf_text(uploaded_file)
    statement_type = detect_statement_type(text)
    statement_year = infer_statement_year(text)

    st.write(f"识别账单类型：{statement_type}")

    if statement_type == "boa_deposit":
        result = parse_boa_deposit_pdf(text, statement_year)
        if not result.empty:
            return result
        st.info("未匹配标准银行月结单格式，正在使用AI识别...")
        return parse_pdf_with_ai(text, client, statement_year)

    if statement_type == "boa_credit_card":
        result = parse_boa_credit_card_pdf(text, statement_year)
        if not result.empty:
            return result
        st.info("信用卡规则解析失败，正在使用AI识别...")
        return parse_pdf_with_ai(text, client, statement_year)

    if statement_type == "amazon_synchrony":
        result = parse_amazon_synchrony_pdf(text, statement_year)
        if not result.empty:
            return result
        return parse_pdf_with_ai(text, client, statement_year)

    if statement_type == "chase_checking":
        result = parse_chase_checking_pdf(text, statement_year)
        if not result.empty:
            return result
        return parse_pdf_with_ai(text, client, statement_year)

    result = parse_generic_pdf(text)
    if not result.empty:
        return result

    st.info("未匹配标准银行月结单格式，正在使用AI识别...")
    return parse_pdf_with_ai(text, client, statement_year)


def read_excel_file(uploaded_file):
    return pd.read_excel(uploaded_file)
