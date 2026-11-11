"""SQL 语义守卫。

只读 SQL 只能保证不会写数据库，不能保证业务语义正确。
这里根据用户问题与 SQL 内容做保守的确定性检查。
"""
import re


TIME_SCOPE_PATTERN = re.compile(
    r"(20\d{2}年|\d{1,2}月|今年|去年|本月|上月|"
    r"本季度|上季度|近\d+天|最近\d+天)",
    re.IGNORECASE,
)

DELIVERY_TIME_BASIS_TERMS = (
    "按送达时间",
    "按签收时间",
    "送达日期",
    "签收日期",
    "按配送时间",
    "按履约时间",
    "配送时间范围",
)

CUSTOMER_INTENT_TERMS = (
    "自然客户",
    "复购",
    "留存",
    "回访",
    "客户数",
    "客户数量",
    "多少客户",
    "客户人数",
)

ONE_TO_MANY_TABLES = {
    "order_items",
    "order_payments",
}

TABLE_REF_PATTERN = re.compile(
    r"\b(?:from|join)\s+"
    r"([`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?)",
    re.IGNORECASE,
)

def validate_sql_semantics(
    question: str,
    sql: str,
) -> None:
    """校验 SQL 与用户问题的关键业务语义是否一致。

    Args:
        question: 原始用户问题。
        sql: 待执行的只读 SQL。

    Raises:
        ValueError: 发现时间字段漂移或客户粒度错误。
    """
    if not question or not question.strip():
        raise ValueError("SQL 语义校验失败：缺少原始用户问题。")

    if not sql or not sql.strip():
        raise ValueError("SQL 语义校验失败：SQL 不能为空。")

    question_text = question.strip()
    sql_lower = sql.casefold()

    has_time_scope = bool(TIME_SCOPE_PATTERN.search(question_text))
    explicit_delivery_time_basis = any(
        term in question_text
        for term in DELIVERY_TIME_BASIS_TERMS
    )

    mentions_purchase_at = "purchase_at" in sql_lower
    mentions_delivered_at = "delivered_at" in sql_lower

    if has_time_scope:
        if explicit_delivery_time_basis:
            if not mentions_delivered_at:
                raise ValueError(
                    "该问题明确按送达时间分析，"
                    "但 SQL 未使用 orders.delivered_at 作为时间条件。"
                )
        else:
            if not mentions_purchase_at:
                raise ValueError(
                    "检测到时间字段漂移：该问题包含明确时间范围，"
                    "且不是按送达时间分析，SQL 必须使用 "
                    "orders.purchase_at 作为默认时间字段。"
                    "只有延迟、准时或明确按送达时间分析时，"
                    "才可以使用 delivered_at 作为判断条件。"
                )

    customer_intent = any(
        term in question_text
        for term in CUSTOMER_INTENT_TERMS
    )

    if customer_intent:
        has_customer_id = bool(
            re.search(r"\bcustomer_id\b", sql_lower)
        )
        has_customer_unique_id = (
            "customer_unique_id" in sql_lower
        )

        if has_customer_id and not has_customer_unique_id:
            raise ValueError(
                "检测到客户粒度错误：自然客户、复购、留存、回访"
                "和客户数量必须按 customers.customer_unique_id 去重，"
                "不能只使用 customer_id。"
            )

    # 时间范围存在时，再检查明显缺少时间过滤的 SQL。
    if has_time_scope and not (
        mentions_purchase_at or mentions_delivered_at
    ):
        raise ValueError(
            "该问题包含明确时间范围，但 SQL 未包含任何时间过滤字段。"
            "请使用 orders.purchase_at 或明确说明使用 delivered_at 的原因。"
        )
    # 一对多明细表重复聚合防护。
    referenced_tables = {
        match.group(1).strip('`"[]').casefold()
        for match in TABLE_REF_PATTERN.finditer(sql)
    }

    one_to_many_tables = sorted(
        referenced_tables & ONE_TO_MANY_TABLES
    )

    if len(one_to_many_tables) >= 2:
        sql_head = sql.lstrip().upper()
        uses_cte = sql_head.startswith("WITH")
        has_preaggregation_hint = (
            uses_cte
            and "GROUP BY" in sql.upper()
            and "ORDER_ID" in sql.upper()
        )

        if not has_preaggregation_hint:
            raise ValueError(
                "检测到多个一对多明细表被直接关联："
                f"{one_to_many_tables}。"
                "例如 order_items 和 order_payments 同层 JOIN "
                "后再 SUM(order_items.price) 会造成金额重复。"
                "请先在 CTE 中分别按 order_id 聚合，"
                "再将聚合结果关联。"
            )

