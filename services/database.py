import pandas as pd
import streamlit as st

from transaction_model import build_transaction_record, normalize_text


def transaction_exists(record, supabase):
    unique_key_match = (
        supabase.table("transactions")
        .select("id")
        .eq("user_id", record["user_id"])
        .eq("unique_key", record["unique_key"])
        .limit(1)
        .execute()
    )

    if unique_key_match.data:
        return True

    # Legacy rows may have a unique_key built from MM/DD/YYYY dates.
    field_match = (
        supabase.table("transactions")
        .select("id")
        .eq("user_id", record["user_id"])
        .eq("date", record["date"])
        .eq("description", record["description"])
        .eq("amount", record["amount"])
        .eq("source_file", record["source_file"])
        .limit(1)
        .execute()
    )
    return bool(field_match.data)


def save_transactions_to_supabase(df, user_id, supabase):
    saved_count = 0
    skipped_count = 0

    if df is None or df.empty:
        return saved_count, skipped_count

    for _, row in df.iterrows():
        try:
            record = build_transaction_record(row, user_id)

            if transaction_exists(record, supabase):
                skipped_count += 1
                continue

            supabase.table("transactions").insert(record).execute()
            saved_count += 1

        except Exception as e:
            skipped_count += 1
            description = normalize_text(
                row.get("Description", row.get("description", ""))
            )
            amount = row.get("Amount", row.get("amount", ""))
            st.warning(f"有一条交易保存失败，已跳过：{description} / {amount} / {e}")

    return saved_count, skipped_count


def load_user_transactions(user_id, supabase):
    result = (
        supabase.table("transactions")
        .select("*")
        .eq("user_id", str(user_id))
        .order("date", desc=True)
        .execute()
    )
    return pd.DataFrame(result.data)


def load_user_rules(user_id, supabase):
    result = (
        supabase.table("merchant_rules")
        .select("*")
        .eq("user_id", str(user_id))
        .execute()
    )

    if result.data:
        return pd.DataFrame(result.data)

    return pd.DataFrame(columns=["merchant", "category"])


def load_global_rules(supabase):
    try:
        result = (
            supabase.table("global_merchant_rules")
            .select("merchant,category")
            .execute()
        )
    except Exception:
        return pd.DataFrame(columns=["merchant", "category"])

    if result.data:
        return pd.DataFrame(result.data)

    return pd.DataFrame(columns=["merchant", "category"])


def save_cloud_rule(user_id, merchant, category, supabase):
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
            (
                supabase.table("merchant_rules")
                .update({"category": category})
                .eq("user_id", str(user_id))
                .eq("merchant", merchant)
                .execute()
            )
            return

        supabase.table("merchant_rules").insert({
            "user_id": str(user_id),
            "merchant": merchant,
            "category": category
        }).execute()
    except Exception as e:
        st.warning(f"云端记忆库保存失败：{e}")


def save_global_rule(merchant, category, supabase, source="ai"):
    merchant = str(merchant).upper().strip()
    category = str(category).strip()

    if not merchant or not category or category == "待分类":
        return

    try:
        existing = (
            supabase.table("global_merchant_rules")
            .select("merchant")
            .eq("merchant", merchant)
            .limit(1)
            .execute()
        )

        if existing.data:
            return

        supabase.table("global_merchant_rules").insert({
            "merchant": merchant,
            "category": category,
            "source": source
        }).execute()
    except Exception as e:
        st.warning(f"全局记忆库保存失败：{e}")


def delete_transactions_by_statement(user_id, source_file, supabase):
    (
        supabase.table("transactions")
        .delete()
        .eq("user_id", str(user_id))
        .eq("source_file", source_file)
        .execute()
    )
