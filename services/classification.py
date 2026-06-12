import os

import pandas as pd


RULES_FILE = "merchant_rules.csv"

CATEGORIES = [
    "餐饮", "超市日用品", "交通加油", "住房", "水电网", "保险",
    "医疗", "教育", "电商收入", "电商物流", "电商进货",
    "摄影业务", "软件订阅", "转账还款", "退款", "投资",
    "房贷", "银行手续费", "利息收入", "税费", "其他"
]


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


def ai_classify(description, amount, client):
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
