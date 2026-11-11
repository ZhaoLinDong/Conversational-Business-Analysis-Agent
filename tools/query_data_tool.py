"""
query_data 工具：编排式数据查询 pipeline（LLM 只做意图解析，代码组装 SQL）

Step2 路由根表 → Step3 JOIN 子图 → Step4/5 字段与枚举召回 → Step5.5 时间范围
→ Step6 生成 QueryPlan → Step7 校验(失败带错重试1次)
→ Step8 渲染 SQL → Step9 只读参数化执行 → Step10 返回数据集 JSON
"""
import json
import sqlite3
from typing import NoReturn

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from config import API_KEY, BASE_URL, MODEL_NAME, SQLITE_DB_PATH
from rules.query_plan import (
    MAX_JOIN_TABLES,
    PlanValidationError,
    QueryPlan,
    RootTableChoice,
    load_field_meta,
    load_query_meta,
    validate_plan,
)
from tools.sql_renderer import render_sql
from rules.query_prompts import PLAN_PROMPT, ROUTE_PROMPT
from rules.business_guard import GuardrailViolation

from rules.query_context import (
    build_enum_dict_text,
    build_field_list_text,
    build_join_context,
    build_join_graph_overview,
    build_join_graph_text,
    build_root_tables_text,
)
from rules.structured_output_guard import parse_structured_result

# ========== LLM（意图解析专用，temperature=0 保证稳定） ==========
_llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0,
    timeout=60,
    max_retries=0,
)
_router_llm = _llm.with_structured_output(
    RootTableChoice,
    include_raw=True,
    method="function_calling",
)
_plan_llm = _llm.with_structured_output(
    QueryPlan,
    include_raw=True,
    method="function_calling",
)

def _fail(
    msg: str,
    error_code: str = "QUERY_DATA_ERROR",
    **extra,
) -> NoReturn:
    """统一失败处理：抛出护栏异常。

    由 LangChain 的 ToolErrorMiddleware 捕获，并转换成
    ToolMessage(status="error")，不再作为普通成功结果返回。
    """
    raise GuardrailViolation(
        error_code=error_code,
        message=msg,
        details=extra or None,
    )


@tool
def query_data(question: str) -> str:
    """执行结构化数据查询，返回查询结果数据集（JSON）。
    当用户提出业务数据问题（销售额、订单量、订单状态、客户城市分布、
    延迟订单、商品排名等需要查数据库统计或带出明细的问题）时使用。
    不适用于：闲聊、导入文件、指标口径解释。

    Args:
        question: 用户的业务数据问题原句
    """
    meta = load_query_meta()
    field_meta = load_field_meta()

    # ---- Step2: 路由根表 ----
    root_tables_text = build_root_tables_text(meta)
    join_graph_overview = build_join_graph_overview(meta)
    try:
        route_result = _router_llm.invoke(
            ROUTE_PROMPT.format(
                root_tables=root_tables_text,
                join_graph=join_graph_overview,
                question=question,
            )
        )
    except Exception as exc:
        return _fail(
            f"路由模型调用失败: {type(exc).__name__}",
            error_code="ROUTER_CALL_ERROR",
        )

    route, route_error = parse_structured_result(
        route_result,
        "查询路由",
    )
    if route is None:
        return _fail(
            route_error or "查询路由未返回可解析结果",
            error_code="ROUTER_PARSE_ERROR",
        )

    if (
            not route.matched
            or route.root_table
            not in {t["table"] for t in meta["root_tables"]}
    ):
        return _fail(
            f"无法理解该查询，问题与候选根表不匹配。路由理由: {route.reason}",
            error_code="ROUTE_NOT_MATCHED",
        )

    # ---- Step3: 根表可达 JOIN 子图 ----
    root = route.root_table

    subgraph, candidate_tables = build_join_context(
        root_table=root,
        meta=meta,
        max_join_depth=MAX_JOIN_TABLES - 1,
    )

    # ---- Step4/5: 字段与枚举召回 ----
    field_list_text = build_field_list_text(
        field_meta=field_meta,
        candidate_tables=candidate_tables,
        enum_dict=meta.get("enum_dict", {}),
    )

    enum_dict_text = build_enum_dict_text(
        meta=meta,
        candidate_tables=candidate_tables,
    )

    join_graph_text = build_join_graph_text(subgraph)

    # ---- Step5.5: 数据时间范围（供模型消解"4月""上个月"等相对时间） ----
    time_range_text = "未知"
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            row = conn.execute(
                'SELECT MIN("purchase_at"), MAX("purchase_at") FROM "orders"'
            ).fetchone()
            if row and row[0]:
                time_range_text = f"{row[0][:10]} ~ {row[1][:10]}"
    except sqlite3.Error:
        pass

    # ---- Step6: 生成 QueryPlan（校验失败带错重试 1 次） ----
    plan = None
    connected = None
    resolved = None
    retry_feedback = ""

    for attempt in range(2):
        try:
            plan_result = _plan_llm.invoke(
                PLAN_PROMPT.format(
                    root_table=root,
                    join_graph=join_graph_text,
                    field_list=field_list_text,
                    enum_dict=enum_dict_text,
                    data_time_range=time_range_text,
                    question=question,
                    retry_feedback=retry_feedback,
                    max_join_tables=MAX_JOIN_TABLES,
                )
            )
        except Exception as exc:
            return _fail(
                f"计划模型调用失败: {type(exc).__name__}",
                error_code="PLAN_CALL_ERROR",
                root_table=root,
            )

        plan, plan_error = parse_structured_result(
            plan_result,
            "查询计划生成",
        )

        if plan is None:
            if attempt == 1:
                return _fail(
                    plan_error or "查询计划未返回可解析结果",
                    error_code="PLAN_PARSE_ERROR",
                    root_table=root,
                )

            retry_feedback = (
                "【上一次没有返回可解析的查询计划】\n"
                f"{plan_error or '模型未返回结构化结果'}"
            )
            continue

        try:
            connected, resolved = validate_plan(plan, meta, field_meta)
            break
        except PlanValidationError as exc:
            if attempt == 1:
                return _fail(
                    f"查询计划校验失败: {exc}",
                    error_code="PLAN_VALIDATION_ERROR",
                    root_table=root,
                )

            retry_feedback = (
                "【上一次计划被拒绝，原因如下，请修正后重新输出】\n"
                f"{exc}"
            )
        except Exception as exc:
            return _fail(
                f"计划校验异常: {type(exc).__name__}",
                error_code="PLAN_VALIDATION_CRASH",
                root_table=root,
            )

    if plan is None or connected is None or resolved is None:
        return _fail(
            "未能生成有效的查询计划",
            error_code="PLAN_EMPTY",
        )

    # ---- Step8: 渲染 SQL ----
    try:
        sql, params = render_sql(plan, resolved, field_meta)
    except Exception as exc:
        return _fail(f"SQL 渲染失败: {exc}")

    # ---- Step9: 只读参数化执行 ----
    conn = None
    try:
        db_posix = SQLITE_DB_PATH.replace("\\", "/")
        conn = sqlite3.connect(f"file:{db_posix}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        rows_raw = conn.execute(sql, params).fetchall()
        rows = [dict(r) for r in rows_raw]
        columns = list(rows_raw[0].keys()) if rows_raw else []
    except sqlite3.Error as exc:
        return _fail(f"SQL 执行失败: {exc}", sql=sql, params=params)
    finally:
        if conn:
            conn.close()

    # ---- Step10: 返回数据集（润色交给 Agent） ----
    MODEL_ROW_CAP = 50   # 喂给模型的行数上限，全量数据靠 row_count 传达
    total_rows = len(rows)
    rows_shown = rows[:MODEL_ROW_CAP]
    return json.dumps(
        {
            "ok": True,
            "columns": columns,
            "rows": rows_shown,
            "row_count": total_rows,
            "rows_shown": len(rows_shown),
            "rows_truncated": total_rows > len(rows_shown),
            "sql": sql,
            "connected_tables": connected,
            "hint": (
                f"数据集共 {total_rows} 行，已展示前 {len(rows_shown)} 行。"
                + (
                    "【重要】行数超过 50，展示的只是样例，不代表全部！"
                    "绝对不能据此得出'没有/不存在/全部都是'类结论。"
                    "如需全局统计，请改用聚合查询（COUNT/SUM+GROUP BY）。"
                    if total_rows > len(rows_shown) else ""
                )
                + "用户问'多少/合计/平均'时优先用聚合口径回答或基于 row_count 回答；"
                "只允许引用数据集内的数字，不要编造"
            ),
        },
        ensure_ascii=False,
        default=str,
    )