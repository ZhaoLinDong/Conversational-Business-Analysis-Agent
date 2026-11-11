"""业务能力路由器。

职责：
1. 使用 LLM 判断用户问题意图；
2. 判断是否需要查询具体业务数据；
3. 对比所需能力与当前 Schema；
4. 输出正常回答、带约束回答或拒答决策；
5. 不维护关键词式语义分类规则。

硬安全限制仍由 SQL 工具和数据库执行层负责。
"""
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from config import API_KEY, BASE_URL, MODEL_NAME
from rules.metric_registry import load_metric_definitions
from rules.output_schemas import BusinessAgentAnswer
from rules.query_plan import load_field_meta
from rules.structured_output_guard import parse_structured_result


logger = logging.getLogger(__name__)


class QuestionScopeDecision(BaseModel):
    """LLM 对用户问题意图和能力范围的结构化判断。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "data_result",
        "explanation",
        "causal_analysis",
        "prediction",
        "privacy",
        "other",
    ] = Field(
        description=(
            "整体问题意图："
            "data_result=请求具体数据结果；"
            "explanation=解释原理、机制、定义、区别或原因；"
            "causal_analysis=询问因果结论；"
            "prediction=要求预测未来；"
            "privacy=请求敏感个人信息；"
            "other=其他。"
        )
    )

    needs_data_query: bool = Field(
        description=(
            "回答该问题是否必须查询数据库或计算具体业务数据。"
            "纯原理解释通常为 false。"
        )
    )

    required_data_elements: list[str] = Field(
        default_factory=list,
        description=(
            "完成用户请求所必需的数据元素、字段或指标，"
            "例如成本、退款金额、商品成交金额。"
        ),
    )

    missing_data_elements: list[str] = Field(
        default_factory=list,
        description=(
            "用户请求需要但当前 Schema 不具备的数据元素。"
        ),
    )

    response_mode: Literal[
        "answer",
        "answer_with_caution",
        "refuse",
    ] = Field(
        description=(
            "answer=正常回答；"
            "answer_with_caution=允许回答但必须附加约束；"
            "refuse=当前能力不足，直接说明无法完成。"
        )
    )

    reason: str = Field(
        description="做出该判断的简短业务理由。",
    )

    answer_constraints: list[str] = Field(
        default_factory=list,
        description=(
            "answer_with_caution 时必须注入给主 Agent 的回答约束，"
            "例如只能描述相关性、不得声称因果关系。"
        ),
    )


_scope_llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0,
    timeout=30,
    max_retries=0,
).with_structured_output(
    QuestionScopeDecision,
    include_raw=True,
    method="function_calling",
)


def build_capability_summary() -> str:
    """构建供能力路由器使用的紧凑 Schema 摘要。"""
    lines: list[str] = ["【可用业务字段】"]

    try:
        field_meta = load_field_meta()
    except Exception:
        field_meta = {}

    for field_key in sorted(field_meta):
        info = field_meta[field_key]
        name = info.get("中文名称", field_key)
        meaning = info.get("业务含义", "").strip()
        if len(meaning) > 80:
            meaning = meaning[:80] + "…"
        lines.append(f"- {field_key}: {name}；{meaning}")

    lines.append("")
    lines.append("【已有标准指标】")

    try:
        metrics = load_metric_definitions()
    except Exception:
        metrics = {}

    for metric_id in sorted(metrics):
        metric = metrics[metric_id]
        description = metric.description.strip()
        if len(description) > 120:
            description = description[:120] + "…"
        lines.append(
            f"- {metric.metric_id}: {metric.name}；{description}"
        )

    summary = "\n".join(lines)
    if len(summary) > 12000:
        summary = summary[:12000] + "\n…（Schema 摘要已截断）"
    return summary


def assess_question_scope(
    question: str,
    capability_summary: str | None = None,
) -> QuestionScopeDecision | None:
    """使用 LLM 判断用户问题意图和数据能力需求。"""
    if not question or not question.strip():
        return None

    summary = capability_summary or build_capability_summary()

    prompt = f"""你是业务能力路由器，不负责回答用户问题。

请根据用户问题的整体语义，判断：
1. 用户是在请求具体数据结果，还是在解释原理、机制、定义、区别、关系或原因？
2. 如果需要业务数据，完成该问题需要哪些数据元素？
3. 当前已有字段和指标是否具备这些能力？
4. 是否需要因果限制、预测限制或隐私拒绝？

不要根据单个关键词做判断，要以问题整体意图为准。

【当前可用能力】
{summary}

【用户问题】
{question}

输出要求：
- data_result：请求具体数值、统计、排名、金额、数量、占比、趋势等结果；
- explanation：解释机制、原理、定义、区别、表关系、JOIN、聚合、重复原因等；
- causal_analysis：询问因果关系或要求判断谁导致谁；
- prediction：询问未来预测或未来走势；
- privacy：请求敏感个人信息；
- other：其他。

判断规则：
- 原理解释类问题不需要具体数据结果时，needs_data_query=false；
- 请求具体数据结果时，needs_data_query=true；
- 如果所需数据元素在 Schema 中不存在，应标记到 missing_data_elements；
- 需要具体数据但能力缺失时，response_mode=refuse；
- 因果问题允许回答，但 response_mode=answer_with_caution，并给出限制约束；
- 预测问题可以 answer_with_caution 或 refuse；
- 隐私请求应 refuse。
"""

    try:
        result = _scope_llm.invoke(prompt)
    except Exception as exc:
        logger.warning(
            "能力路由器调用失败: %s",
            type(exc).__name__,
        )
        return None

    decision, error_message = parse_structured_result(
        result,
        "问题能力范围判断",
    )

    if decision is None:
        logger.warning(
            "能力路由器未返回可解析结果: %s",
            error_message,
        )
        return None

    # 确定性一致性修正：需要数据且确认缺能力时，不能正常回答。
    if (
        decision.needs_data_query
        and decision.missing_data_elements
        and decision.response_mode == "answer"
    ):
        decision.response_mode = "refuse"

    # 带约束回答必须至少有一条约束。
    if (
        decision.response_mode == "answer_with_caution"
        and not decision.answer_constraints
    ):
        decision.answer_constraints.append(
            "只能描述数据差异和相关性，不得断言因果关系或保证预测结果。"
        )

    return decision


def build_refusal_answer(
    decision: QuestionScopeDecision,
) -> BusinessAgentAnswer:
    """把能力路由器的拒答决策转换为结构化回答。"""
    reason = decision.reason.strip() or "当前数据能力不足，无法完成该查询。"
    explanation = [reason]

    if decision.missing_data_elements:
        explanation.append(
            "当前缺少所需数据能力："
            + "、".join(decision.missing_data_elements)
        )

    return BusinessAgentAnswer(
        answer=reason,
        answer_type="error",
        explanation=explanation,
        caveats=list(decision.missing_data_elements),
    )


def apply_scope_constraints(
    user_input: str,
    decision: QuestionScopeDecision | None,
) -> str:
    """把 Scope Router 的执行约束注入本轮用户输入。"""
    if decision is None:
        return user_input

    constraints: list[str] = []

    # 不需要数据查询的问题，禁止调用数据查询和 SQL 工具。
    if (
        decision.intent == "explanation"
        or not decision.needs_data_query
    ):
        constraints.append(
            "本轮不需要具体数据结果："
            "禁止调用 query_business_metric、query_data、run_readonly_sql；"
            "不要执行 SQL 验证原理。"
        )
        constraints.append(
            "可以使用 search_business_schema 或 search_knowledge_base 获取必要背景，"
            "但获取后应直接解释，不要反复验证。"
        )

    # 带约束回答，例如因果分析。
    if decision.response_mode == "answer_with_caution":
        caution_constraints = decision.answer_constraints or [
            "只能描述数据差异和相关性，不得断言因果关系或保证预测结果。"
        ]
        constraints.extend(caution_constraints)

    if not constraints:
        return user_input

    constraint_text = "\n".join(
        f"- {constraint}"
        for constraint in constraints
    )
    return (
        f"{user_input}\n\n"
        "【本轮执行与回答约束】\n"
        f"{constraint_text}"
    )