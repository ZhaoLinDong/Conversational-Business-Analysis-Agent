"""
智能体流式事件解析
- consume_agent_stream: 消费 agent.stream() 输出，提取回复、工具事件、结构化结果和中断请求
- extract_interrupt_payload: 把 LangGraph interrupt 对象转成 GUI 可展示的字典
"""
import json
from typing import Any, Optional

from langchain_core.messages import AIMessage, ToolMessage

from rules.output_schemas import AgentRunResult, BusinessAgentAnswer

# response_format 的内部伪工具名（结构化输出通过它实现，不展示给用户）
_STRUCTURED_TOOL_NAME = "BusinessAgentAnswer"


def extract_interrupt_payload(interrupt) -> dict[str, Any]:
    """LangGraph interrupt 对象 → GUI 可展示的字典"""
    value = interrupt.value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        value = {}

    return {
        "interrupt_id": getattr(interrupt, "id", ""),
        "type": value.get("type", "tool_approval"),
        "tool": value.get("name"),
        "arguments": value.get("args", {}),
        "description": value.get("description", ""),
        "allowed_decisions": ["approve", "reject"],
    }


def _extract_ai_text(message: AIMessage) -> str:
    """提取 AIMessage 的纯文本内容（兼容多段内容结构）"""
    content = message.content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        )
    return content


def _read_structured_response(agent, config: dict) -> Optional[BusinessAgentAnswer]:
    """从最终 State 兜底读取结构化响应（流式事件没拿到时使用）"""
    try:
        state = agent.get_state(config)
        candidate = state.values.get("structured_response")
        if candidate is None:
            return None
        if isinstance(candidate, BusinessAgentAnswer):
            return candidate
        return BusinessAgentAnswer.model_validate(candidate)
    except Exception:
        return None


def consume_agent_stream(agent, config: dict, stream, on_event=None) -> AgentRunResult:
    """
    消费 agent.stream() 的输出流。

    Args:
        agent: create_agent 生成的智能体（用于兜底读取最终 State）
        config: 会话配置（thread_id）
        stream: agent.stream() 返回的迭代器
        on_event: 过程事件回调，可为 None

    Returns:
        AgentRunResult
    """
    final_content = ""
    structured_response: Optional[BusinessAgentAnswer] = None
    pending_interrupts: list[dict[str, Any]] = []

    for chunk in stream:
        # 人工审核中断
        if "__interrupt__" in chunk:
            for interrupt in chunk["__interrupt__"]:
                pending_interrupts.append(extract_interrupt_payload(interrupt))
            continue

        for node_output in chunk.values():
            if not isinstance(node_output, dict):
                continue

            # 结构化响应
            candidate = node_output.get("structured_response")
            if candidate is not None:
                if isinstance(candidate, BusinessAgentAnswer):
                    structured_response = candidate
                else:
                    structured_response = BusinessAgentAnswer.model_validate(candidate)

            # 消息事件
            for msg in node_output.get("messages", []):
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for call in msg.tool_calls:
                        if call["name"] == _STRUCTURED_TOOL_NAME:
                            continue
                        if on_event:
                            on_event(
                                f"🔧 调用工具: {call['name']}\n"
                                f"    参数: {call['args']}"
                            )

                elif isinstance(msg, ToolMessage):
                    if getattr(msg, "name", "") == _STRUCTURED_TOOL_NAME:
                        continue

                    if on_event:
                        raw_content = str(msg.content)

                        # ToolErrorMiddleware 返回的通常是 JSON，
                        # 优先展示面向用户的 message 字段。
                        visible_content = raw_content
                        try:
                            payload = json.loads(raw_content)
                            if isinstance(payload, dict):
                                visible_content = (
                                    payload.get("message")
                                    or payload.get("error")
                                    or raw_content
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass

                        preview = str(visible_content)[:500].replace("\n", " ")

                        if getattr(msg, "status", None) == "error":
                            on_event(f"❌ 工具执行失败: {preview}")
                        else:
                            on_event(f"✅ 工具返回: {preview}")

                elif isinstance(msg, AIMessage):
                    text = _extract_ai_text(msg)
                    if text:
                        final_content = text

    # 流式事件没拿到结构化响应时，从最终 State 兜底
    if not pending_interrupts and structured_response is None:
        structured_response = _read_structured_response(agent, config)

    # 有待审核请求，返回中断状态
    if pending_interrupts:
        if on_event:
            on_event("⏸️ 查询需要人工确认")
        return AgentRunResult(
            status="interrupted",
            content=final_content,
            structured_response=None,
            interrupts=pending_interrupts,
        )

    if structured_response is not None and not final_content.strip():
        final_content = structured_response.answer

    # 防止模型空回复
    if not final_content.strip():
        final_content = (
            "模型未返回有效结果，请稍后重试，"
            "或把问题拆成更小的分析步骤。"
        )

    # 将内部调用上限错误转换为用户可读提示
    internal_limit_markers = (
        "Model call limits exceeded",
        "Tool call limit exceeded",
        "tool call limit reached",
        "run limit exceeded",
    )
    if any(
        marker in final_content
        for marker in internal_limit_markers
    ):
        final_content = (
            "本次查询步骤超过系统上限，"
            "请缩小时间范围、减少维度，或拆成多个问题后重试。"
        )

    return AgentRunResult(
        status="completed",
        content=final_content,
        structured_response=structured_response,
    )