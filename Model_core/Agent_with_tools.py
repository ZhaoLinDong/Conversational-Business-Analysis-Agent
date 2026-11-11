"""
带工具调用的智能体
- 本文件只负责"装配"（模型/工具/中间件）与"对外接口"（chat/resume/reset/switch_model）
- 流式事件解析在 Model_core/stream_events.py
- 输出契约（BusinessAgentAnswer / AgentRunResult）在 rules/output_schemas.py
"""
import time
from typing import Optional, Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    ToolErrorMiddleware,
    ToolCallLimitMiddleware,
    ModelCallLimitMiddleware,
)
from langchain_openai import ChatOpenAI
from langgraph.types import Command

from Memory import trim_messages, create_checkpointer
from rules.output_schemas import BusinessAgentAnswer, AgentRunResult
from rules.business_guard import (
    create_human_review_middleware,
    on_model_failure,
    on_tool_error,
)
from Model_core.stream_events import consume_agent_stream
from tools import ALL_TOOLS
from config import API_KEY, BASE_URL, MODEL_NAME, SYSTEM_PROMPT
from rules.capability_guard import (
    apply_scope_constraints,
    assess_question_scope,
    build_refusal_answer,
)

class ToolAgent:
    """业务分析智能体：数据导入 + 指标查询 + 条件式人工审核"""

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        system_prompt: str = SYSTEM_PROMPT,
        tools: Optional[list] = None,
        thread_id: str = "1",
        persistent_checkpoint: bool = False,
    ):
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.tools = tools if tools is not None else ALL_TOOLS
        self.thread_id = thread_id

        self.llm = ChatOpenAI(
            model=model_name,
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.2,
            timeout=60,
            max_retries=0,
        )
        self.checkpointer = create_checkpointer(persistent=persistent_checkpoint)
        self.agent = self._build_agent()

    def _build_agent(self):
        """装配智能体：模型 + 工具 + 结构化输出 + 中间件护栏。"""
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
            middleware=[
                trim_messages,

                # 模型瞬时故障最多重试 1 次；避免长时间指数退避。
                ModelRetryMiddleware(
                    max_retries=1,
                    backoff_factor=2.0,
                    initial_delay=1.0,
                    max_delay=4.0,
                    jitter=True,
                    on_failure=on_model_failure,
                ),

                # 人工审核放在工具错误处理之前。
                create_human_review_middleware(),

                ToolErrorMiddleware(on_error=on_tool_error),

                # 全局工具调用上限。
                ToolCallLimitMiddleware(
                    run_limit=8,
                    exit_behavior="end",
                ),

                # 单个工具上限，防止同一工具反复循环。
                ToolCallLimitMiddleware(
                    tool_name="search_business_metrics",
                    run_limit=3,
                    exit_behavior="end",
                ),
                ToolCallLimitMiddleware(
                    tool_name="query_business_metric",
                    run_limit=4,
                    exit_behavior="end",
                ),
                ToolCallLimitMiddleware(
                    tool_name="search_business_schema",
                    run_limit=3,
                    exit_behavior="end",
                ),
                ToolCallLimitMiddleware(
                    tool_name="run_readonly_sql",
                    run_limit=3,
                    exit_behavior="end",
                ),
                ToolCallLimitMiddleware(
                    tool_name="query_data",
                    run_limit=3,
                    exit_behavior="end",
                ),

                # 模型调用上限；超过后立即结束，不再无限重试。
                ModelCallLimitMiddleware(
                    run_limit=10,
                    exit_behavior="end",
                ),
            ],
            response_format=ToolStrategy(
                BusinessAgentAnswer,
                handle_errors=True,
            ),
            checkpointer=self.checkpointer,
        )

    def _config(self) -> dict:
        """会话配置"""
        return {"configurable": {"thread_id": self.thread_id}}

    def chat(self, user_input: str, on_event=None) -> AgentRunResult:
        """发送用户输入，返回本轮运行结果。"""
        scope = assess_question_scope(user_input)

        if (
                scope is not None
                and scope.response_mode == "refuse"
        ):
            refusal = build_refusal_answer(scope)
            if on_event:
                on_event("⚠️ 当前问题超出数据能力范围，已返回限制说明。")
            return AgentRunResult(
                status="completed",
                content=refusal.answer,
                structured_response=refusal,
            )

        effective_input = apply_scope_constraints(
            user_input,
            scope,
        )

        stream = self.agent.stream(
            {"messages": [{"role": "user", "content": effective_input}]},
            self._config(),
            stream_mode="updates",
        )
        return consume_agent_stream(
            self.agent,
            self._config(),
            stream,
            on_event=on_event,
        )

    def resume(
        self,
        interrupt_id: str,
        decision: str,
        message: str | None = None,
        on_event=None,
    ) -> AgentRunResult:
        """人工审核后继续执行。decision: approve / reject"""
        if decision not in {"approve", "reject"}:
            raise ValueError("decision 只能是 approve 或 reject")

        resume_value: dict[str, Any] = {"type": decision}
        if decision == "reject" and message:
            resume_value["message"] = message

        stream = self.agent.stream(
            Command(resume={interrupt_id: resume_value}),
            self._config(),
            stream_mode="updates",
        )
        return consume_agent_stream(self.agent, self._config(), stream, on_event=on_event)

    def reset(self):
        """重置对话：换一个新会话"""
        self.thread_id = f"user_{time.time()}"
        print(f"对话已重置，新会话: {self.thread_id}")

    def switch_model(self, model_name: str):
        """切换模型并重建智能体（记忆保留在 checkpointer 中）"""
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.2,
            timeout=60,
            max_retries=0,
        )
        self.model_name = model_name
        self.agent = self._build_agent()