import streamlit as st
import pdfplumber
import pandas as pd
import re

st.title("PDF账单测试")

uploaded_file = st.file_uploader(
    "上传PDF账单",
    type=["pdf"]
)

if uploaded_file:

    text = ""

    with pdfplumber.open(uploaded_file) as pdf:

        for page in pdf.pages:

            page_text = page.extract_text()

            if page_text:
                text += page_text + "\n"

    st.subheader("提取文字")

    st.text_area(
        "PDF内容",
        text,
        height=300
    )

    # 提取交易记录

    pattern = r"(\d{2}/\d{2}/\d{4})(.*?)([-]?\$?[\d,]+\.\d{2})"

    matches = re.findall(
        pattern,
        text,
        re.DOTALL
    )

    transactions = []

    for date, desc, amount in matches:

        desc = desc.replace("\n", " ").strip()

        amount = (
            amount.replace("$", "")
                  .replace(",", "")
        )

        transactions.append([
            date,
            desc,
            float(amount)
        ])

    if transactions:

        df = pd.DataFrame(
            transactions,
            columns=[
                "Date",
                "Description",
                "Amount"
            ]
        )

        st.subheader("识别出的交易")

        st.dataframe(df)

    else:

        st.warning("未识别到交易记录")