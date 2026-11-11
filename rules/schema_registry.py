"""业务 Schema 检索。

根据用户问题检索相关表、字段、枚举和 JOIN 关系，
供通用只读 SQL 查询工具使用。
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_CATALOG_PATH = ROOT / "rules" / "metadata" / "business_catalog.json"
QUERY_META_PATH = ROOT / "rules" / "metadata" / "query_meta.json"


JOIN_CARDINALITY = {
    "orders_order_items": "one_to_many",
    "orders_payments": "one_to_many",
    "orders_reviews": "one_to_one_or_zero",
    "orders_customers": "many_to_one",
    "order_items_products": "many_to_one",
    "order_items_sellers": "many_to_one",
    "products_categories": "many_to_one",
    "customers_geolocation": "many_to_one",
}


def search_business_schema(
    query: str,
    limit_tables: int = 4,
    limit_fields: int = 60,
) -> dict:
    """根据问题检索相关业务 Schema。

    Args:
        query: 用户问题或业务关键词。
        limit_tables: 最多返回的相关表数量。
        limit_fields: 最多返回的相关字段数量。

    Returns:
        包含 tables、fields、joins、枚举和时间字段说明的字典。
    """
    if not query or not query.strip():
        return {
            "ok": True,
            "query": query,
            "tables": [],
            "fields": [],
            "joins": [],
            "enums": {},
            "default_time_fields": [],
            "hint": "查询为空，请提供业务关键词。",
        }

    if not 1 <= limit_tables <= 10:
        raise ValueError("limit_tables 必须在 1 到 10 之间")

    if not 1 <= limit_fields <= 200:
        raise ValueError("limit_fields 必须在 1 到 200 之间")

    catalog = json.loads(
        BUSINESS_CATALOG_PATH.read_text(encoding="utf-8-sig")
    )
    query_meta = json.loads(
        QUERY_META_PATH.read_text(encoding="utf-8-sig")
    )

    tables_meta = catalog.get("tables", {})
    fields_meta = catalog.get("fields", {})
    enum_dict = query_meta.get("enum_dict", {})

    def normalize(text: str) -> str:
        return "".join(
            ch.casefold()
            for ch in str(text)
            if ch.isalnum()
        )

    def bigrams(text: str) -> set[str]:
        if len(text) < 2:
            return {text} if text else set()
        return {
            text[index:index + 2]
            for index in range(len(text) - 1)
        }

    query_text = normalize(query)
    query_grams = bigrams(query_text)

    def text_score(candidate_text: str) -> float:
        candidate = normalize(candidate_text)
        if not candidate:
            return 0.0

        if query_text == candidate:
            return 2.0

        score = 0.0
        if query_text in candidate or candidate in query_text:
            score += 1.0

        candidate_grams = bigrams(candidate)
        if query_grams and candidate_grams:
            overlap = len(query_grams & candidate_grams)
            score += (
                2.0
                * overlap
                / (len(query_grams) + len(candidate_grams))
            )

        return score

    field_scores: dict[str, float] = {}
    table_field_scores: dict[str, list[float]] = {}

    for field_key, info in fields_meta.items():
        table_name = field_key.split(".", 1)[0]
        if table_name not in tables_meta:
            continue

        table_info = tables_meta.get(table_name, {})
        field_name = info.get("中文名称", "")
        field_meaning = info.get("业务含义", "")
        field_range = info.get("取值范围", "")
        table_name_zh = table_info.get("中文名称", "")

        score = max(
            text_score(field_key) * 1.2,
            text_score(field_name) * 2.5,
            text_score(field_meaning) * 2.0,
            text_score(field_range) * 1.0,
            text_score(table_name_zh) * 1.0,
        )

        if score > 0:
            field_scores[field_key] = score
            table_field_scores.setdefault(table_name, []).append(score)

    table_scores: dict[str, float] = {}

    for table_name, table_info in tables_meta.items():
        table_name_zh = table_info.get("中文名称", "")
        table_module = table_info.get("所属模块", "")
        table_type = table_info.get("表类型", "")

        base_score = max(
            text_score(table_name) * 1.5,
            text_score(table_name_zh) * 2.5,
            text_score(table_module) * 0.8,
            text_score(table_type) * 0.5,
        )

        field_score_list = sorted(
            table_field_scores.get(table_name, []),
            reverse=True,
        )
        if field_score_list:
            base_score += field_score_list[0] * 1.5
            base_score += sum(field_score_list[1:4]) * 0.2

        if base_score > 0:
            table_scores[table_name] = base_score

    selected_tables = [
        table_name
        for table_name, _ in sorted(
            table_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit_tables]
    ]

    if not selected_tables:
        return {
            "ok": True,
            "query": query,
            "tables": [],
            "fields": [],
            "joins": [],
            "enums": {},
            "default_time_fields": [],
            "hint": (
                "没有检索到相关表。"
                "请换用更具体的业务关键词，或向用户澄清数据范围。"
            ),
        }

    selected_table_set = set(selected_tables)

    returned_tables = []
    for table_name in selected_tables:
        info = tables_meta.get(table_name, {})
        returned_tables.append(
            {
                "table": table_name,
                "name": info.get("中文名称", table_name),
                "module": info.get("所属模块", ""),
                "table_type": info.get("表类型", ""),
                "description": info.get("表说明", ""),
            }
        )

    candidate_fields = [
        (field_key, field_scores.get(field_key, 0.0))
        for field_key in fields_meta
        if field_key.split(".", 1)[0] in selected_table_set
    ]
    candidate_fields.sort(
        key=lambda item: (-item[1], item[0])
    )

    selected_fields = candidate_fields[:limit_fields]

    returned_fields = []
    default_time_fields: list[str] = []

    for field_key, _ in selected_fields:
        info = fields_meta[field_key]
        table_name, col = field_key.split(".", 1)
        meaning = info.get("业务含义", "")
        is_time_field = (
            col.endswith("_at")
            or "日期" in info.get("中文名称", "")
            or "时间" in info.get("中文名称", "")
        )
        is_default_time_field = (
            is_time_field
            and "默认" in meaning
            and "时间" in meaning
        )

        field_item = {
            "field": field_key,
            "table": table_name,
            "col": col,
            "name": info.get("中文名称", col),
            "data_type": info.get("数据类型", ""),
            "meaning": meaning,
            "value_range": info.get("取值范围", ""),
            "sensitive_level": info.get("敏感级别", ""),
            "is_time_field": is_time_field,
            "is_default_time_field": is_default_time_field,
        }

        if field_key in enum_dict:
            field_item["enum_values"] = enum_dict[field_key]

        if is_default_time_field:
            default_time_fields.append(field_key)

        returned_fields.append(field_item)

    returned_joins = []
    for edge in query_meta.get("join_graph", []):
        left_table = edge.get("left_table")
        right_table = edge.get("right_table")
        if (
            left_table not in selected_table_set
            and right_table not in selected_table_set
        ):
            continue

        join_id = edge.get("join_id", "")
        returned_joins.append(
            {
                "join_id": join_id,
                "left_table": left_table,
                "right_table": right_table,
                "join_type": edge.get("join_type", "LEFT JOIN"),
                "on_expr": edge.get("on_expr", ""),
                "description": edge.get("description", ""),
                "cardinality": JOIN_CARDINALITY.get(
                    join_id,
                    "unknown",
                ),
            }
        )

    returned_enums = {
        field_key: values
        for field_key, values in enum_dict.items()
        if field_key.split(".", 1)[0] in selected_table_set
    }

    return {
        "ok": True,
        "query": query,
        "tables": returned_tables,
        "fields": returned_fields,
        "joins": returned_joins,
        "enums": returned_enums,
        "default_time_fields": default_time_fields,
        "hint": (
            "JOIN 的 cardinality 表示表之间的数据基数；"
            "one_to_many 关系下直接聚合多张明细表可能造成金额重复。"
            "写 SQL 时应优先使用明确的时间字段和过滤条件。"
        ),
    }