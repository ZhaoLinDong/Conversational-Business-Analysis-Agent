"""业务指标工具。"""
import json
import sqlite3
from typing import Any

from langchain_core.tools import tool

from rules.business_guard import GuardrailViolation
from rules.metric_compiler import compile_metric_plan
from rules.metric_registry import (
    get_metric_definition,
    search_metric_definitions,
)
from rules.metric_schema import MetricQueryRequest, MetricTimeRange
from rules.query_plan import (
    PlanValidationError,
    load_field_meta,
    load_query_meta,
)
from tools.db_executor import execute_readonly_query
from tools.sql_renderer import render_sql


MODEL_ROW_CAP = 50


@tool
def search_business_metrics(
    query: str,
    limit: int = 5,
) -> str:
    """搜索标准业务指标。

    当用户提出销售额、订单量、延迟率、复购客户数等业务指标问题时，
    应先调用本工具查找标准指标。不要自行猜测指标口径。

    Args:
        query: 用户问题或业务关键词。
        limit: 最多返回的候选数量，建议 1 到 10。
    """
    if not query or not query.strip():
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "candidates": [],
                "count": 0,
                "hint": "查询关键词为空，请提供业务关键词。",
            },
            ensure_ascii=False,
        )

    if limit < 1 or limit > 10:
        raise ValueError("limit 必须在 1 到 10 之间")

    try:
        candidates = search_metric_definitions(
            query=query,
            limit=limit,
        )
    except Exception as exc:
        raise GuardrailViolation(
            error_code="METRIC_SEARCH_ERROR",
            message="业务指标规则加载或搜索失败，请稍后重试。",
            details={"error_type": type(exc).__name__},
        ) from exc

    if candidates:
        hint = (
            "如果候选指标明确，调用 query_business_metric；"
            "如果仍有多个可能指标，先向用户澄清。"
        )
    else:
        hint = (
            "没有匹配到标准指标。"
            "可以向用户澄清业务口径，或使用 query_data 做临时查询。"
        )

    return json.dumps(
        {
            "ok": True,
            "query": query,
            "candidates": candidates,
            "count": len(candidates),
            "hint": hint,
        },
        ensure_ascii=False,
    )


@tool
def query_business_metric(
    metric_id: str,
    question: str,
    start_date: str | None = None,
    end_date: str | None = None,
    dimensions: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> str:
    """执行标准业务指标查询。

    必须先通过 search_business_metrics 获取 metric_id，
    不要自行编造指标编码。
            question: 原始用户问题，用于判断指标适用范围是否匹配。
    Args:
        metric_id: 标准指标编码。
        start_date: 开始时间，格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。
        end_date: 结束时间，格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。
        dimensions: 需要使用的指标维度列表。
        parameters: 指标运行时参数。
        limit: 返回行数上限；不传时使用指标默认值。
    """
    try:
        metric = get_metric_definition(metric_id)

        normalized_start = start_date.strip() if start_date else None
        supports_overall = not (
            bool(metric.group_by)
            and not metric.count_subquery
        )

        overall_intent_terms = (
            "整体",
            "总体",
            "平台整体",
            "全平台",
            "全站",
            "全部订单",
            "总延迟率",
            "整体延迟率",
            "总销售额",
            "整体销售额",
        )
        grouped_intent_terms = (
            "商家",
            "品类",
            "类别",
            "每个",
            "各个",
            "分组",
            "排名",
            "最高",
            "最低",
        )

        has_overall_intent = any(
            term in question
            for term in overall_intent_terms
        )
        has_grouped_intent = any(
            term in question
            for term in grouped_intent_terms
        )

        if (
            has_overall_intent
            and not has_grouped_intent
            and not supports_overall
        ):
            raise GuardrailViolation(
                error_code="METRIC_SCOPE_MISMATCH",
                message=(
                    f"指标 '{metric.name}' 返回的是分组级结果，"
                    "不能用于回答整体指标。"
                    "请改用 search_business_schema + run_readonly_sql，"
                    "直接对事实表做整体聚合。"
                ),
                details={
                    "metric_id": metric.metric_id,
                    "group_by": metric.group_by,
                    "question": question,
                },
            )
        normalized_end = end_date.strip() if end_date else None

        if bool(normalized_start) != bool(normalized_end):
            raise ValueError(
                "start_date 和 end_date 必须同时提供或同时省略"
            )

        time_range = None
        if normalized_start and normalized_end:
            time_range = MetricTimeRange(
                start=normalized_start,
                end=normalized_end,
            )

        request = MetricQueryRequest(
            metric_id=metric_id,
            time_range=time_range,
            dimensions=dimensions or [],
            parameters=parameters or {},
            limit=limit,
        )

        meta = load_query_meta()
        field_meta = load_field_meta()

        plan, connected_tables, resolved_joins = compile_metric_plan(
            metric=metric,
            request=request,
            meta=meta,
            field_meta=field_meta,
        )

        sql, sql_params = render_sql(
            plan,
            resolved_joins,
            field_meta,
        )

        columns, rows = execute_readonly_query(
            sql,
            sql_params,
        )

        total_rows = len(rows)
        rows_shown = rows[:MODEL_ROW_CAP]
        rows_truncated = total_rows > len(rows_shown)

        applied_parameters: dict[str, Any] = {}
        for parameter_name, parameter_spec in metric.parameters.items():
            if parameter_name in request.parameters:
                applied_parameters[parameter_name] = (
                    request.parameters[parameter_name]
                )
            elif parameter_spec.default is not None:
                applied_parameters[parameter_name] = parameter_spec.default

        hint = (
            f"指标 '{metric.name}' 查询完成。"
            "只能引用返回数据中的数字和维度值，不要编造。"
        )
        if rows_truncated:
            hint += (
                f" 数据共 {total_rows} 行，"
                f"当前只展示前 {len(rows_shown)} 行。"
            )

        return json.dumps(
            {
                "ok": True,
                "metric_id": metric.metric_id,
                "metric_name": metric.name,
                "metric_version": metric.version,
                "metric_description": metric.description,
                "columns": columns,
                "rows": rows_shown,
                "row_count": total_rows,
                "rows_shown": len(rows_shown),
                "rows_truncated": rows_truncated,
                "connected_tables": connected_tables,
                "applied_time_range": (
                    request.time_range.model_dump()
                    if request.time_range is not None
                    else None
                ),
                "applied_dimensions": request.dimensions,
                "applied_parameters": applied_parameters,
                "hint": hint,
            },
            ensure_ascii=False,
            default=str,
        )

    except GuardrailViolation:
        raise

    except KeyError as exc:
        raise GuardrailViolation(
            error_code="METRIC_NOT_FOUND",
            message=str(exc),
        ) from exc

    except (ValueError, PlanValidationError) as exc:
        raise GuardrailViolation(
            error_code="METRIC_REQUEST_INVALID",
            message=str(exc)[:1500],
        ) from exc

    except sqlite3.Error as exc:
        raise GuardrailViolation(
            error_code="METRIC_DATABASE_ERROR",
            message="指标查询数据库执行失败，请稍后重试。",
            details={"error_type": type(exc).__name__},
        ) from exc

    except Exception as exc:
        raise GuardrailViolation(
            error_code="METRIC_QUERY_ERROR",
            message="指标查询失败，请稍后重试。",
            details={"error_type": type(exc).__name__},
        ) from exc