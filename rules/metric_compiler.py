"""业务指标规则到 QueryPlan 的确定性编译器。

MetricDefinition 提供固定查询模板；
MetricQueryRequest 提供时间、维度和参数；
最终编译为现有 QueryPlan，并复用统一校验和 SQL Renderer。
"""
from rules.metric_schema import (
    MetricDefinition,
    MetricFilterSpec,
    MetricQueryRequest,
    MetricValueBinding,
)
from rules.query_plan import Predicate, ValueExpr, LogicNode, SelectExpr, WhereNode, HavingCond, QueryPlan, JoinChoice, \
    validate_plan

from typing import Any


def _field_expr(field_key: str) -> ValueExpr:
    """把 "表.字段" 字符串转换成 QueryPlan 字段表达式。

    Args:
        field_key: 格式为 "表.字段"。

    Returns:
        ValueExpr(kind="field", table=..., col=...)。

    Raises:
        ValueError: 字段格式不合法。
    """
    if not isinstance(field_key, str) or not field_key.strip():
        raise ValueError("字段引用不能为空")

    raw = field_key.strip()
    if "." not in raw:
        raise ValueError(
            f"字段引用 '{field_key}' 格式应为 表.字段"
        )

    table, col = raw.split(".", 1)
    if not table.strip() or not col.strip():
        raise ValueError(
            f"字段引用 '{field_key}' 必须同时包含表名和字段名"
        )

    return ValueExpr(
        kind="field",
        table=table.strip(),
        col=col.strip(),
    )

def _resolve_binding_value(
    binding: MetricValueBinding,
    metric: MetricDefinition,
    request: MetricQueryRequest,
) -> Any:
    """解析指标模板中的值绑定。

    Args:
        binding: 固定值或参数引用。
        metric: 当前指标定义。
        request: 当前指标查询请求。

    Returns:
        最终用于过滤或 HAVING 的实际值。

    Raises:
        ValueError: 参数未声明、缺失、类型错误或不在允许值中。
    """
    if binding.kind == "literal":
        return binding.value

    if binding.kind != "param":
        raise ValueError(
            f"不支持的 MetricValueBinding.kind: {binding.kind}"
        )

    parameter_name = binding.value
    if not isinstance(parameter_name, str) or not parameter_name.strip():
        raise ValueError("参数引用必须是非空参数名字符串")

    if parameter_name not in metric.parameters:
        raise ValueError(
            f"指标 '{metric.metric_id}' 未声明参数 "
            f"'{parameter_name}'"
        )

    parameter_spec = metric.parameters[parameter_name]

    if parameter_name in request.parameters:
        value = request.parameters[parameter_name]
    elif parameter_spec.default is not None:
        value = parameter_spec.default
    else:
        raise ValueError(
            f"指标 '{metric.metric_id}' 缺少参数 "
            f"'{parameter_name}'"
        )

    if (
        parameter_spec.allowed_values is not None
        and value not in parameter_spec.allowed_values
    ):
        raise ValueError(
            f"参数 '{parameter_name}' 的值 {value!r} 不在允许值中: "
            f"{parameter_spec.allowed_values}"
        )

    if parameter_spec.data_type == "string":
        if not isinstance(value, str):
            raise ValueError(
                f"参数 '{parameter_name}' 必须是字符串，"
                f"当前类型为 {type(value).__name__}"
            )

    elif parameter_spec.data_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"参数 '{parameter_name}' 必须是整数，"
                f"当前类型为 {type(value).__name__}"
            )

    elif parameter_spec.data_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"参数 '{parameter_name}' 必须是数字，"
                f"当前类型为 {type(value).__name__}"
            )

    elif parameter_spec.data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(
                f"参数 '{parameter_name}' 必须是布尔值，"
                f"当前类型为 {type(value).__name__}"
            )

    elif parameter_spec.data_type == "date":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"参数 '{parameter_name}' 必须是非空日期字符串"
            )

    else:
        raise ValueError(
            f"参数 '{parameter_name}' 的类型 "
            f"'{parameter_spec.data_type}' 不受支持"
        )

    return value

def _build_filter_predicate(
    filter_spec: MetricFilterSpec,
    metric: MetricDefinition,
    request: MetricQueryRequest,
) -> Predicate:
    """把指标过滤模板编译成 QueryPlan Predicate。"""
    raw_field = filter_spec.field.strip()
    if "." not in raw_field:
        raise ValueError(
            f"过滤字段 '{filter_spec.field}' 格式应为 表.字段"
        )

    table, col = raw_field.split(".", 1)
    if not table.strip() or not col.strip():
        raise ValueError(
            f"过滤字段 '{filter_spec.field}' 必须同时包含表名和字段名"
        )

    table = table.strip()
    col = col.strip()

    if filter_spec.value_col is not None:
        value_col = filter_spec.value_col.strip()
        if "." not in value_col:
            raise ValueError(
                f"过滤条件 value_col '{filter_spec.value_col}' "
                "格式应为 表.字段"
            )

        value_table, value_column = value_col.split(".", 1)
        if not value_table.strip() or not value_column.strip():
            raise ValueError(
                f"过滤条件 value_col '{filter_spec.value_col}' "
                "必须同时包含表名和字段名"
            )

        return Predicate(
            type="predicate",
            table=table,
            col=col,
            op=filter_spec.op,
            value=None,
            value_col=(
                f"{value_table.strip()}.{value_column.strip()}"
            ),
        )

    if filter_spec.value is None:
        raise ValueError(
            f"过滤条件 '{filter_spec.field}' 缺少 value 或 value_col"
        )

    value = _resolve_binding_value(
        filter_spec.value,
        metric,
        request,
    )

    if value is None:
        raise ValueError(
            f"过滤条件 '{filter_spec.field}' 的解析结果为 None；"
            "当前不支持 IS NULL，请改用合法比较值"
        )

    return Predicate(
        type="predicate",
        table=table,
        col=col,
        op=filter_spec.op,
        value=value,
    )

def _build_where_ast(
    metric: MetricDefinition,
    request: MetricQueryRequest,
) -> WhereNode | None:
    """构建指标查询的 WHERE AST。

    Args:
        metric: 指标定义。
        request: 指标查询请求。

    Returns:
        QueryPlan 条件树；无过滤条件时返回 None。
    """
    predicates: list[Predicate] = []

    for filter_spec in metric.filters:
        predicates.append(
            _build_filter_predicate(
                filter_spec=filter_spec,
                metric=metric,
                request=request,
            )
        )

    if request.time_range is not None:
        if metric.time is None:
            raise ValueError(
                f"指标 '{metric.metric_id}' 未声明时间字段，"
                "不能接受 time_range"
            )

        time_field = metric.time.field.strip()
        if "." not in time_field:
            raise ValueError(
                f"指标 '{metric.metric_id}' 的时间字段 "
                f"'{time_field}' 格式应为 表.字段"
            )

        table, col = time_field.split(".", 1)
        if not table.strip() or not col.strip():
            raise ValueError(
                f"指标 '{metric.metric_id}' 的时间字段 "
                f"'{time_field}' 必须同时包含表名和字段名"
            )

        predicates.append(
            Predicate(
                type="predicate",
                table=table.strip(),
                col=col.strip(),
                op=">=",
                value=request.time_range.start,
            )
        )
        predicates.append(
            Predicate(
                type="predicate",
                table=table.strip(),
                col=col.strip(),
                op="<=",
                value=request.time_range.end,
            )
        )

    elif metric.time is not None and metric.time.required:
        raise ValueError(
            f"指标 '{metric.metric_id}' 必须提供 time_range"
        )

    if not predicates:
        return None

    if len(predicates) == 1:
        return predicates[0]

    return LogicNode(
        type="logic",
        op="AND",
        children=predicates,
    )

def _build_select_and_group_by(
    metric: MetricDefinition,
    request: MetricQueryRequest,
) -> tuple[list[SelectExpr], list[str]]:
    """构建 SELECT 输出列和 GROUP BY 字段。

    Args:
        metric: 指标定义。
        request: 指标查询请求。

    Returns:
        tuple: (输出列列表, GROUP BY 字段列表)
    """
    dimension_map = {
        dimension.dimension_id: dimension
        for dimension in metric.dimensions
    }

    def resolve_dimension(dimension_id: str):
        """按精确名称或大小写不敏感方式解析维度。"""
        if dimension_id in dimension_map:
            return dimension_map[dimension_id]

        normalized = dimension_id.casefold()
        for current_id, dimension in dimension_map.items():
            if current_id.casefold() == normalized:
                return dimension

        available = sorted(dimension_map)
        raise ValueError(
            f"指标 '{metric.metric_id}' 不支持维度 "
            f"'{dimension_id}'。可用维度: {available}"
        )

    select_exprs = list(metric.select_exprs)
    group_by = list(metric.group_by)

    seen_aliases: set[str] = set()
    for item in select_exprs:
        alias_key = item.alias.casefold()
        if alias_key in seen_aliases:
            raise ValueError(
                f"指标 '{metric.metric_id}' 固定输出列存在重复别名: "
                f"{item.alias}"
            )
        seen_aliases.add(alias_key)

    seen_group_fields: set[str] = set()
    for field_key in group_by:
        seen_group_fields.add(field_key.casefold())

    for dimension_id in request.dimensions:
        dimension = resolve_dimension(dimension_id)

        for item in dimension.select_exprs:
            alias_key = item.alias.casefold()
            if alias_key in seen_aliases:
                raise ValueError(
                    f"指标 '{metric.metric_id}' 维度 "
                    f"'{dimension.dimension_id}' 的输出列别名 "
                    f"'{item.alias}' 与已有输出列冲突"
                )
            seen_aliases.add(alias_key)
            select_exprs.append(item)

        for field_key in dimension.group_by:
            normalized_field = field_key.casefold()
            if normalized_field in seen_group_fields:
                continue
            seen_group_fields.add(normalized_field)
            group_by.append(field_key)

    return select_exprs, group_by

def _build_having_ast(
    metric: MetricDefinition,
    request: MetricQueryRequest,
) -> list[HavingCond] | None:
    """构建指标查询的 HAVING 条件。"""
    if not metric.having:
        return None

    having_conditions: list[HavingCond] = []

    for index, condition in enumerate(metric.having):
        value = _resolve_binding_value(
            condition.value,
            metric,
            request,
        )

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"指标 '{metric.metric_id}' 的 having[{index}] "
                f"值必须是数字，当前为 {type(value).__name__}"
            )

        having_conditions.append(
            HavingCond(
                select_alias=condition.select_alias,
                op=condition.op,
                value=value,
            )
        )

    return having_conditions

def compile_metric_plan(
    metric: MetricDefinition,
    request: MetricQueryRequest,
    meta: dict,
    field_meta: dict,
) -> tuple[QueryPlan, list[str], list[dict]]:
    """把指标定义和查询请求编译成完整 QueryPlan。

    Args:
        metric: 指标定义。
        request: 指标查询请求。
        meta: query_meta 元数据。
        field_meta: 业务字段元数据。

    Returns:
        tuple:
            - 完整 QueryPlan；
            - 已连接表列表；
            - 定向后的 JOIN 列表。

    Raises:
        ValueError: 请求与指标不匹配或参数非法。
        PlanValidationError: 编译后的 QueryPlan 未通过校验。
    """
    if request.metric_id.casefold() != metric.metric_id.casefold():
        raise ValueError(
            f"请求 metric_id '{request.metric_id}' "
            f"与指标定义 '{metric.metric_id}' 不一致"
        )

    unknown_parameters = (
        set(request.parameters) - set(metric.parameters)
    )
    if unknown_parameters:
        raise ValueError(
            f"指标 '{metric.metric_id}' 收到未声明参数: "
            f"{sorted(unknown_parameters)}。"
            f"可用参数: {sorted(metric.parameters)}"
        )

    missing_required_parameters: list[str] = []
    for parameter_name, parameter_spec in metric.parameters.items():
        if (
            parameter_spec.required
            and parameter_name not in request.parameters
            and parameter_spec.default is None
        ):
            missing_required_parameters.append(parameter_name)

    if missing_required_parameters:
        raise ValueError(
            f"指标 '{metric.metric_id}' 缺少必填参数: "
            f"{missing_required_parameters}"
        )

    select_exprs, group_by = _build_select_and_group_by(
        metric=metric,
        request=request,
    )

    where_ast = _build_where_ast(
        metric=metric,
        request=request,
    )

    having_ast = _build_having_ast(
        metric=metric,
        request=request,
    )

    plan = QueryPlan(
        root_table=metric.base_table,
        join_list=[
            JoinChoice(join_id=join_id)
            for join_id in metric.joins
        ],
        select_exprs=select_exprs,
        group_by=group_by,
        where_ast=where_ast,
        having_ast=having_ast,
        order_by=list(metric.order_by),
        count_subquery=metric.count_subquery,
        limit=(
            request.limit
            if request.limit is not None
            else metric.default_limit
        ),
    )

    connected, resolved_joins = validate_plan(
        plan,
        meta,
        field_meta,
    )

    return plan, connected, resolved_joins