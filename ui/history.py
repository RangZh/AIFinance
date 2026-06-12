import streamlit as st


def render_history(history_for_summary):
    st.subheader("历史交易")

    if history_for_summary.empty:
        st.info("你的云端账户还没有保存交易")
        return

    st.write(f"云端账户中共有 {len(history_for_summary)} 条交易记录")
    filtered_df = history_for_summary.copy()

    col1, col2 = st.columns(2)

    with col1:
        categories = ["全部"] + sorted(
            filtered_df["Category"].dropna().unique().tolist()
        )
        selected_category = st.selectbox(
            "按类别筛选",
            categories,
            key="history_category_filter"
        )

    with col2:
        if "SourceFile" in filtered_df.columns:
            files = ["全部"] + sorted(
                filtered_df["SourceFile"].dropna().unique().tolist()
            )
            selected_file = st.selectbox(
                "按账单文件筛选",
                files,
                key="history_file_filter"
            )
        else:
            selected_file = "全部"

    if selected_category != "全部":
        filtered_df = filtered_df[
            filtered_df["Category"] == selected_category
        ]

    if selected_file != "全部" and "SourceFile" in filtered_df.columns:
        filtered_df = filtered_df[
            filtered_df["SourceFile"] == selected_file
        ]

    st.caption("默认显示最近100条交易")
    st.dataframe(filtered_df.head(100), width="stretch")
