"""通用只读 SQL 查询工具。"""
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from rules.business_guard import GuardrailViolation
from rules.sql_semantic_guard import validate_sql_semantics
from tools.db_executor import execute_readonly_query


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_CATALOG_PATH = ROOT / "rules" / "metadata" / "business_catalog.json"

MAX_SQL_LENGTH = 12000
MAX_RETURN_ROWS = 500

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|vacuum|pragma|reindex|analyze|"
    r"trigger|view|index|sqlite_master|sqlite_schema"
    r")\b",
    re.IGNORECASE,
)

TABLE_REF_PATTERN = re.compile(
    r"\b(?:from|join)\s+"
    r"([`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?)",
    re.IGNORECASE,
)

CTE_NAME_PATTERN = re.compile(
    r"(?:\bWITH\b(?:\s+RECURSIVE)?|,)\s*"
    r"([`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?)"
    r"\s+AS\s*\(",
    re.IGNORECASE,
)

def _normalize_table_ref(raw_table: str) -> str:
    """去掉表名周围可能存在的 SQL 标识符引号。"""
    return raw_table.strip().strip('`"[]')


@tool
def run_readonly_sql(
    sql: str,
    question: str,
    params: list[Any] | None = None,
    purpose: str | None = None,
    max_rows: int = 200,
) -> str:
    """执行经过安全检查的只读 SELECT 查询。

    只适用于标准指标无法覆盖的临时分析。调用前应先使用
    search_business_schema 获取相关表、字段、JOIN 和时间字段。

    Args:
        sql: 只读 SELECT 语句。值应尽量使用 ? 占位符。
        question: 必须传原始用户问题，用于时间字段和客户粒度语义校验。
        params: 与 SQL 中 ? 占位符对应的参数列表。
        purpose: 本次查询目的，便于审计和结果解释。
        max_rows: 返回给模型的最大行数，1 到 500。
    """
    if not sql or not sql.strip():
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message="SQL 不能为空。",
        )

    if len(sql) > MAX_SQL_LENGTH:
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message=f"SQL 长度不能超过 {MAX_SQL_LENGTH} 个字符。",
        )

    if max_rows < 1 or max_rows > MAX_RETURN_ROWS:
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message=f"max_rows 必须在 1 到 {MAX_RETURN_ROWS} 之间。",
        )

    normalized_sql = sql.strip()
    sql_head = normalized_sql.upper()

    if not (
            sql_head.startswith("SELECT")
            or sql_head.startswith("WITH")
    ):
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message="只允许执行 SELECT 或 WITH ... SELECT 查询。",
        )

    try:
        validate_sql_semantics(
            question=question,
            sql=normalized_sql,
        )
    except ValueError as exc:
        raise GuardrailViolation(
            error_code="SQL_SEMANTIC_ERROR",
            message=str(exc)[:1500],
            details={
                "question": question,
                "purpose": purpose,
            },
        ) from exc

    try:
        catalog = json.loads(
            BUSINESS_CATALOG_PATH.read_text(encoding="utf-8-sig")
        )
    except Exception as exc:
        raise GuardrailViolation(
            error_code="SCHEMA_LOAD_ERROR",
            message="业务 Schema 加载失败，无法校验 SQL。",
            details={"error_type": type(exc).__name__},
        ) from exc

    allowed_tables = {
        table_name.casefold()
        for table_name in catalog.get("tables", {})
    }

    # CTE 名称属于查询内部临时表，不是数据库真实表。
    cte_names = {
        _normalize_table_ref(match.group(1)).casefold()
        for match in CTE_NAME_PATTERN.finditer(normalized_sql)
    }
    allowed_tables.update(cte_names)

    referenced_tables = {
        _normalize_table_ref(match.group(1)).casefold()
        for match in TABLE_REF_PATTERN.finditer(normalized_sql)
    }

    if not referenced_tables:
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message="无法识别 SQL 中的业务表名。",
        )

    forbidden_tables = sorted(
        referenced_tables - allowed_tables
    )
    if forbidden_tables:
        raise GuardrailViolation(
            error_code="SQL_TABLE_FORBIDDEN",
            message=f"SQL 访问了未登记的业务表: {forbidden_tables}",
        )

    query_params = params or []

    try:
        columns, all_rows = execute_readonly_query(
            normalized_sql,
            query_params,
        )
    except TimeoutError as exc:
        raise GuardrailViolation(
            error_code="SQL_TIMEOUT",
            message=str(exc),
            details={
                "purpose": purpose,
            },
        ) from exc
    except ValueError as exc:
        raise GuardrailViolation(
            error_code="SQL_INVALID",
            message=str(exc)[:1500],
            details={
                "purpose": purpose,
            },
        ) from exc
    except sqlite3.Error as exc:
        raise GuardrailViolation(
            error_code="SQL_EXECUTION_ERROR",
            message=f"SQL 执行失败: {str(exc)[:1500]}",
            details={
                "purpose": purpose,
            },
        ) from exc

    total_rows = len(all_rows)
    shown_rows = all_rows[:max_rows]
    rows_truncated = total_rows > len(shown_rows)

    return json.dumps(
        {
            "ok": True,
            "purpose": purpose,
            "columns": columns,
            "rows": shown_rows,
            "row_count": total_rows,
            "rows_shown": len(shown_rows),
            "rows_truncated": rows_truncated,
            "sql": normalized_sql,
            "params": query_params,
            "hint": (
                "只允许引用本次返回的数据。"
                "如果需要继续细化，可基于同一会话追问或重新调用 Schema 工具。"
                "rows_truncated=true 表示返回结果仅包含前若干行。"
            ),
        },
        ensure_ascii=False,
        default=str,
    )