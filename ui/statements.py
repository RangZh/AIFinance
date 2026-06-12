import streamlit as st

from services.database import delete_transactions_by_statement


def render_statements(history_df, user_id, supabase):
    st.subheader("账单管理")

    if history_df.empty:
        st.info("你的云端账户还没有保存交易")
        return

    files = history_df["source_file"].dropna().unique().tolist()

    if len(files) == 0:
        st.info("没有可删除的账单文件")
        return

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

    selected_file = st.selectbox(
        "选择要删除的账单文件",
        files,
        key="delete_statement_file"
    )
    file_count = len(history_df[history_df["source_file"] == selected_file])

    st.warning(f"这个账单包含 {file_count} 条交易，删除后不可恢复")
    confirm_delete = st.checkbox(
        "我确认要删除这个账单",
        key="confirm_delete_statement"
    )

    if st.button("删除这个账单", key="delete_statement_button"):
        if not confirm_delete:
            st.error("请先勾选确认删除")
            return

        delete_transactions_by_statement(user_id, selected_file, supabase)
        st.success(f"已删除：{selected_file}")
        st.rerun()
