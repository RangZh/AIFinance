import re
from collections import Counter
from datetime import datetime

import pandas as pd


TRANSACTION_DB_FIELDS = (
    "user_id",
    "date",
    "description",
    "amount",
    "category",
    "source_file",
    "month",
    "unique_key",
)


def normalize_date(date_text, default_year=None):
    if date_text is None or pd.isna(date_text):
        return ""

    if isinstance(date_text, (pd.Timestamp, datetime)):
        return pd.Timestamp(date_text).strftime("%Y-%m-%d")

    date_text = str(date_text).strip()
    if not date_text:
        return ""

    date_formats = (
        ("%Y-%m-%d", None),
        ("%m/%d/%Y", None),
        ("%m/%d/%y", None),
        ("%m/%d", default_year),
    )

    for date_format, year in date_formats:
        candidate = date_text
        if date_format == "%m/%d":
            if year is None:
                continue
            candidate = f"{date_text}/{year}"
            date_format = "%m/%d/%Y"

        try:
            return datetime.strptime(candidate, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return ""


def infer_statement_year(text):
    current_year = datetime.now().year
    years = [
        int(year)
        for year in re.findall(r"\b(20\d{2})\b", str(text))
        if 2000 <= int(year) <= current_year + 1
    ]

    if not years:
        return None

    year_counts = Counter(years)
    return max(year_counts, key=lambda year: (year_counts[year], year))


def normalize_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def make_cloud_unique_key(row, user_id):
    date_value = normalize_date(row.get("Date", row.get("date")))
    description = normalize_text(
        row.get("Description", row.get("description", ""))
    )
    amount = float(row.get("Amount", row.get("amount")))
    source_file = normalize_text(
        row.get("SourceFile", row.get("source_file", ""))
    )

    return "|".join([
        str(user_id),
        date_value,
        description,
        str(amount),
        source_file,
    ])


def build_transaction_record(row, user_id):
    date_value = normalize_date(row.get("Date", row.get("date")))
    description = normalize_text(
        row.get("Description", row.get("description", ""))
    )
    category = normalize_text(row.get("Category", row.get("category", "")))
    source_file = normalize_text(
        row.get("SourceFile", row.get("source_file", ""))
    )
    amount = pd.to_numeric(row.get("Amount", row.get("amount")), errors="coerce")

    if not date_value:
        raise ValueError("交易日期无效或缺少年份")
    if not description:
        raise ValueError("交易描述不能为空")
    if pd.isna(amount):
        raise ValueError("交易金额无效")
    if not category:
        raise ValueError("交易类别不能为空")

    normalized_row = {
        "Date": date_value,
        "Description": description,
        "Amount": float(amount),
        "SourceFile": source_file,
    }

    record = {
        "user_id": str(user_id),
        "date": date_value,
        "description": description,
        "amount": float(amount),
        "category": category,
        "source_file": source_file,
        "month": date_value[:7],
        "unique_key": make_cloud_unique_key(normalized_row, user_id),
    }

    return {field: record[field] for field in TRANSACTION_DB_FIELDS}
