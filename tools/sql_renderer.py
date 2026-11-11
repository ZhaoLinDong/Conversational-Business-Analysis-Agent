"""
通用 SQL 渲染器：QueryPlan(JSON) → (SQL 文本, 参数数组)
固定逻辑，业务新增表/关联只改元数据，不改本文件
所有值一律 ? 占位符参数绑定，杜绝注入
"""
from typing import Any, List, Tuple

from rules.query_plan import QueryPlan, ValueExpr, SelectExpr

def _render_node(node, params) -> str:
    """递归渲染条件树：值进参数数组（防注入），字段比较直接拼列名（列名经白名单）"""
    if node.type == "predicate":
        left = f'"{node.table}"."{node.col}"'
        if node.value_col is not None:
            rt, rc = node.value_col.split(".", 1)
            return f'({left} {node.op} "{rt}"."{rc}")'
        params.append(node.value)
        return f"({left} {node.op} ?)"
    inner = f" {node.op} ".join(_render_node(c, params) for c in node.children)
    return f"({inner})"

def _render_value_expr(expr: ValueExpr, params: List[Any]) -> str:
    """把 ValueExpr 渲染成安全的 SQL 表达式。

    params 与渲染顺序保持一致；所有字面量都使用 ? 参数绑定。
    """
    if expr.kind == "field":
        if not expr.table or not expr.col:
            raise ValueError("field 表达式缺少 table 或 col")
        return f'"{expr.table}"."{expr.col}"'

    if expr.kind == "literal":
        params.append(expr.value)
        return "?"

    if expr.kind == "aggregate":
        if expr.agg is None or not expr.table or not expr.col:
            raise ValueError("aggregate 表达式缺少 agg、table 或 col")

        col_sql = "*" if expr.col == "*" else f'"{expr.table}"."{expr.col}"'

        if col_sql == "*" and expr.agg != "COUNT":
            raise ValueError("只有 COUNT 聚合允许 col='*'")

        if expr.filter_ast is not None:
            condition_sql = _render_node(expr.filter_ast, params)

            if expr.agg == "COUNT":
                if expr.col == "*":
                    return f"COUNT(CASE WHEN {condition_sql} THEN 1 END)"
                return f"COUNT(CASE WHEN {condition_sql} THEN {col_sql} END)"

            if expr.agg == "COUNT_DISTINCT":
                return (
                    "COUNT(DISTINCT CASE WHEN "
                    f"{condition_sql} THEN {col_sql} END)"
                )

            if expr.agg == "SUM":
                return (
                    f"SUM(CASE WHEN {condition_sql} "
                    f"THEN {col_sql} ELSE 0 END)"
                )

            return f"{expr.agg}(CASE WHEN {condition_sql} THEN {col_sql} END)"

        if expr.agg == "COUNT":
            if expr.col == "*":
                return "COUNT(*)"
            return f"COUNT({col_sql})"

        if expr.agg == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {col_sql})"

        return f"{expr.agg}({col_sql})"

    if expr.kind == "binary":
        if expr.op not in {"+", "-", "*", "/"}:
            raise ValueError(f"不支持的二元运算符: {expr.op}")
        if expr.left is None or expr.right is None:
            raise ValueError("binary 表达式缺少 left 或 right")

        left_sql = _render_value_expr(expr.left, params)
        right_sql = _render_value_expr(expr.right, params)

        if expr.op == "/":
            return (
                f"(CAST({left_sql} AS REAL) "
                f"/ NULLIF({right_sql}, 0))"
            )

        return f"({left_sql} {expr.op} {right_sql})"

    raise ValueError(f"不支持的表达式类型: {expr.kind}")


def _render_select_expr(item: SelectExpr, params: List[Any]) -> str:
    """渲染单个输出列：<表达式> AS "别名"。"""
    expr_sql = _render_value_expr(item.expr, params)
    alias = item.alias.replace('"', '""')
    return f'{expr_sql} AS "{alias}"'


def render_sql(
    plan: QueryPlan,
    resolved_joins: List[dict],
    field_meta: dict,
) -> Tuple[str, List[Any]]:
    """渲染最终 SQL 和参数数组。

    JOIN 使用校验层定向后的 resolved_joins。
    field_meta 保留在签名中，兼容现有调用方。
    """
    params: List[Any] = []

    select_parts = [
        _render_select_expr(item, params)
        for item in plan.select_exprs
    ]
    if not select_parts:
        raise ValueError("select_exprs 为空，无法渲染 SQL")

    sql = f'SELECT {", ".join(select_parts)} FROM "{plan.root_table}"'

    for join in resolved_joins:
        sql += (
            f' {join["join_type"]} "{join["right_table"]}" '
            f'ON {join["on_expr"]}'
        )

    if plan.where_ast is not None:
        sql += f" WHERE {_render_node(plan.where_ast, params)}"

    if plan.group_by:
        group_parts: list[str] = []
        for field_key in plan.group_by:
            if "." not in field_key:
                raise ValueError(
                    f"group_by 字段 '{field_key}' 格式应为 表.字段"
                )
            table, col = field_key.split(".", 1)
            group_parts.append(f'"{table}"."{col}"')
        sql += f" GROUP BY {', '.join(group_parts)}"

    if plan.having_ast:
        having_parts: list[str] = []
        for condition in plan.having_ast:
            alias = condition.select_alias.replace('"', '""')
            params.append(condition.value)
            having_parts.append(f'"{alias}" {condition.op} ?')
        sql += f" HAVING {' AND '.join(having_parts)}"

    # 分组再计数时，排序对最终结果没有意义，因此不写入内层 SQL。
    if plan.order_by and not plan.count_subquery:
        order_parts: list[str] = []
        for item in plan.order_by:
            alias = item.select_alias.replace('"', '""')
            order_parts.append(f'"{alias}" {item.direction}')
        sql += f" ORDER BY {', '.join(order_parts)}"

    if plan.count_subquery:
        return f'SELECT COUNT(*) AS "分组数" FROM ({sql})', params

    if plan.limit is not None:
        sql += f" LIMIT {int(plan.limit)}"

    return sql, params