import os

import streamlit as st
from openai import OpenAI
from supabase import create_client

from ui.dashboard import render_dashboard
from ui.history import render_history
from ui.monthly_report import render_monthly_report
from ui.statements import render_statements
from ui.upload import render_upload


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

    if st.sidebar.button("测试 Supabase 连接"):
        supabase.table("transactions").select("*").limit(1).execute()
        st.sidebar.success("Supabase 连接成功")

    return user.id


# ======================
# 页面主体
# ======================
user_id = login_ui()

st.title("AI记账软件")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "总览",
    "上传账单",
    "月度报告",
    "历史交易",
    "账单管理"
])

with tab1:
    history_df, history_for_summary = render_dashboard(user_id, supabase)

with tab2:
    render_upload(user_id, supabase, client)

with tab3:
    render_monthly_report(history_for_summary, client)

with tab4:
    render_history(history_for_summary)

with tab5:
    render_statements(history_df, user_id, supabase)
