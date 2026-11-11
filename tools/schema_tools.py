"""通用 Schema 检索工具。"""
import json

from langchain_core.tools import tool

from rules.business_guard import GuardrailViolation
from rules.schema_registry import (
    search_business_schema as search_schema,
)


@tool
def search_business_schema(
    query: str,
    limit_tables: int = 4,
    limit_fields: int = 60,
) -> str:
    """根据用户问题检索相关业务表、字段、JOIN 和时间字段。

    当标准指标无法覆盖问题，或者需要临时维度、复杂过滤、
    自定义聚合和多表组合时，先调用本工具获取 Schema。

    Args:
        query: 用户问题或业务关键词，例如“商家销售额和已送达订单数”。
        limit_tables: 最多返回的相关表数量，建议 2 到 6。
        limit_fields: 最多返回的相关字段数量，建议 20 到 80。
    """
    try:
        result = search_schema(
            query=query,
            limit_tables=limit_tables,
            limit_fields=limit_fields,
        )
    except Exception as exc:
        raise GuardrailViolation(
            error_code="SCHEMA_SEARCH_ERROR",
            message="业务 Schema 检索失败，请稍后重试。",
            details={"error_type": type(exc).__name__},
        ) from exc

    return json.dumps(
        result,
        ensure_ascii=False,
        default=str,
    )