"""
消息裁剪中间件
在每轮调用模型前自动裁剪对话历史，防止 token 超限：
- 永远保留第一条消息（关键上下文锚点，如用户自我介绍）
- 保留最近 N 条消息
- 裁剪结果会写回 checkpointer（被裁的消息真实删除）
"""
from typing import Any

from langchain.agents.middleware import before_model, AgentState, Runtime
from langchain_core.messages import RemoveMessage, trim_messages as trim_chat_messages
from langgraph.graph.message import REMOVE_ALL_MESSAGES


# 保留的近似 token 上限；超过后按最近消息裁剪
MAX_CONTEXT_TOKENS = 12000


@before_model
def trim_messages(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """每轮调模型前裁剪历史：保留第一条 + 最近若干条"""
    messages = state["messages"]
    if not messages:
        return None


    trimmed = trim_chat_messages(
        messages,
        max_tokens=MAX_CONTEXT_TOKENS,
        token_counter="approximate",
        strategy="last",
        start_on="human",
        include_system=True,
        allow_partial=False,
    )

    # 极端情况下如果原生裁剪返回空列表，不要清空历史，保持原状。
    if not trimmed:
        return None

    if trimmed == messages:
        return None

    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *trimmed,
        ]
    }