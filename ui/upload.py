import pandas as pd
import streamlit as st

from services import (
    CATEGORIES,
    ai_classify,
    classify_with_memory,
)
from services.database import (
    load_global_rules,
    load_user_rules,
    save_cloud_rule,
    save_global_rule,
    save_transactions_to_supabase,
)
from statement_parsers import read_excel_file, read_pdf_file
from transaction_model import normalize_date
from ui.dashboard import show_financial_summary


def _transaction_key(row):
    return (
        str(row.get("Date", "")),
        str(row.get("Description", "")),
        str(row.get("Amount", "")),
        str(row.get("SourceFile", "")),
    )


def render_upload(user_id, supabase, client):
    st.subheader("上传账单")

    uploaded_files = st.file_uploader(
        "上传一个或多个Excel/PDF账单",
        type=["xlsx", "pdf"],
        accept_multiple_files=True,
        key="upload_files"
    )

    if not uploaded_files:
        st.info("请先上传账单，然后再保存到账户")
        return

    upload_signature = tuple(
        (uploaded_file.name, getattr(uploaded_file, "size", None))
        for uploaded_file in uploaded_files
    )
    if st.session_state.get("upload_signature") != upload_signature:
        st.session_state["upload_signature"] = upload_signature
        st.session_state["upload_category_overrides"] = {}
        st.session_state["upload_processed_other"] = set()

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
        return

    df = pd.concat(all_dataframes, ignore_index=True)

    required_columns = {"Date", "Description", "Amount"}
    if not required_columns.issubset(df.columns):
        st.error("账单缺少 Date、Description 或 Amount 列，无法处理")
        return

    df["Date"] = df["Date"].apply(normalize_date)
    invalid_date_count = int((df["Date"] == "").sum())
    if invalid_date_count:
        st.warning(f"已跳过 {invalid_date_count} 条日期无效或缺少年份的交易")
        df = df[df["Date"] != ""].copy()

    user_rules_df = load_user_rules(user_id, supabase)
    global_rules_df = load_global_rules(supabase)

    def classify_transaction(description):
        return classify_with_memory(
            description,
            user_rules_df,
            global_rules_df
        )

    df["Category"] = df["Description"].apply(classify_transaction)
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    df = df.dropna(subset=["Amount"])

    category_overrides = st.session_state.get(
        "upload_category_overrides",
        {}
    )
    for index, row in df.iterrows():
        category = category_overrides.get(_transaction_key(row))
        if category:
            df.at[index, "Category"] = category

    pending_df = df[df["Category"] == "待分类"]
    processed_other = st.session_state.get("upload_processed_other", set())
    other_df = df[
        (df["Category"] == "其他")
        & ~df.apply(_transaction_key, axis=1).isin(processed_other)
    ]

    if st.session_state.pop("upload_ai_classified", False):
        st.success("AI分类完成，并已写入记忆库")

    if st.session_state.pop("upload_ai_other_classified", False):
        st.success("“其他”交易已完成 AI 分类")

    if len(pending_df) > 0:
        st.warning(f"发现 {len(pending_df)} 条待分类交易")
        if st.button("AI处理待分类", key="ai_classify_pending"):
            with st.spinner("AI正在分类，请稍等..."):
                for index, row in pending_df.iterrows():
                    description = row["Description"]
                    amount = row["Amount"]
                    category = ai_classify(description, amount, client)
                    df.at[index, "Category"] = category
                    category_overrides[_transaction_key(row)] = category
                    save_cloud_rule(
                        user_id,
                        description,
                        category,
                        supabase
                    )
                    save_global_rule(
                        description,
                        category,
                        supabase
                    )
            st.session_state["upload_category_overrides"] = category_overrides
            st.session_state["upload_ai_classified"] = True
            st.session_state["upload_editor_revision"] = (
                st.session_state.get("upload_editor_revision", 0) + 1
            )
            st.rerun()

    if len(other_df) > 0:
        st.warning(f"发现 {len(other_df)} 条“其他”交易可由 AI 重新识别")
        if st.button("AI处理其他", key="ai_classify_other"):
            with st.spinner("AI正在重新识别“其他”交易，请稍等..."):
                for index, row in other_df.iterrows():
                    description = row["Description"]
                    amount = row["Amount"]
                    category = ai_classify(
                        description,
                        amount,
                        client,
                        use_quick_rules=False
                    )
                    transaction_key = _transaction_key(row)
                    df.at[index, "Category"] = category
                    category_overrides[transaction_key] = category
                    processed_other.add(transaction_key)
                    save_cloud_rule(
                        user_id,
                        description,
                        category,
                        supabase
                    )
                    save_global_rule(
                        description,
                        category,
                        supabase
                    )
            st.session_state["upload_category_overrides"] = category_overrides
            st.session_state["upload_processed_other"] = processed_other
            st.session_state["upload_ai_other_classified"] = True
            st.session_state["upload_editor_revision"] = (
                st.session_state.get("upload_editor_revision", 0) + 1
            )
            st.rerun()

    st.subheader("分类结果（可手动修改类别）")
    st.caption(
        "系统会优先使用记忆库分类；不确定的交易会标记为待分类，"
        "可点击 AI处理待分类；已有的“其他”可点击 AI处理其他重新识别。"
    )
    st.caption(
        "Category 列可以修改：双击分类单元格打开列表，"
        "修改后请点击保存分类到记忆库。"
    )
    editor_revision = st.session_state.get("upload_editor_revision", 0)
    edited_df = st.data_editor(
        df,
        width="stretch",
        num_rows="fixed",
        column_config={
            "Category": st.column_config.SelectboxColumn(
                "Category（双击修改）",
                options=CATEGORIES,
                required=True
            )
        },
        key=f"edited_upload_df_{editor_revision}"
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("保存分类到记忆库", key="save_rules_from_edit"):
            for _, row in edited_df.iterrows():
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
            st.success(
                f"保存完成：新增 {saved_count} 条，跳过重复 {skipped_count} 条"
            )

    st.divider()
    show_financial_summary(edited_df, key_prefix="upload")
