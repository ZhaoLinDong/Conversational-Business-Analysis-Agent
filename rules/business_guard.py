from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date
from typing import Any

from langchain.agents.middleware import HumanInTheLoopMiddleware


logger = logging.getLogger(__name__)


def _csv_env(name: str, default: str = "") -> set[str]:
    value = os.getenv(name, default)
    return {item.strip() for item in value.split(",") if item.strip()}


# 需要人工确认的指标，后续按业务添加
HIGH_RISK_METRICS = _csv_env("HIGH_RISK_METRICS")

# 普通查询自动执行的最大范围
MAX_AUTO_QUERY_DAYS = int(os.getenv("MAX_AUTO_QUERY_DAYS", "180"))
MAX_AUTO_LIMIT = int(os.getenv("MAX_AUTO_LIMIT", "100"))

# 敏感维度
SENSITIVE_DIMENSIONS = _csv_env(
    "SENSITIVE_DIMENSIONS",
    "customer_id,customer_unique_id",
)


class GuardrailViolation(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        details: Any = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details

    def to_json(self) -> str:
        payload = {
            "ok": False,
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details is not None:
            payload["details"] = self.details
        return json.dumps(payload, ensure_ascii=False, default=str)


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def needs_human_review(request) -> bool:
    """只有高风险或大范围只读查询才进入人工审核。"""
    if request.tool_call.get("name") != "query_business_metric":
        return False

    args = request.tool_call.get("args") or {}
    metric_id = args.get("metric_id", "")

    if metric_id in HIGH_RISK_METRICS:
        return True

    try:
        limit = int(args.get("limit", 100))
    except (TypeError, ValueError):
        return True

    if limit > MAX_AUTO_LIMIT:
        return True

    try:
        start_date = _parse_date(args.get("start_date"))
        end_date = _parse_date(args.get("end_date"))
    except (TypeError, ValueError):
        return True

    if (end_date - start_date).days > MAX_AUTO_QUERY_DAYS:
        return True

    dimensions = set(args.get("dimensions") or [])
    if dimensions & SENSITIVE_DIMENSIONS:
        return True

    return False


def create_human_review_middleware() -> HumanInTheLoopMiddleware:
    """创建人工审核中间件。

    只拦截会修改数据库或外部状态的工具。
    只读查询 query_data 不在这里拦截，避免正常业务查询被频繁打断。
    """
    return HumanInTheLoopMiddleware(
        interrupt_on={
            "import_data_to_db": {
                "allowed_decisions": ["approve", "reject"],
                "description": (
                    "该操作会将本地文件导入 SQLite 数据库，"
                    "可能新增表、追加数据或覆盖已有数据。"
                    "请核对文件路径和目标表名后确认是否执行。"
                ),
            },
            "batch_import_from_folder": {
                "allowed_decisions": ["approve", "reject"],
                "description": (
                    "该操作会批量导入文件夹中的多个数据文件，"
                    "并向 SQLite 数据库写入多张表。"
                    "请核对文件夹路径后确认是否执行。"
                ),
            },
        },
        interrupt_mode="per_call",
    )

def on_model_failure(exc: Exception) -> str:
    """模型调用在重试耗尽后返回统一的安全提示。

    详细异常只写入日志，不直接展示给用户。
    """
    logger.error(
        "模型调用重试耗尽: %s",
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return "模型服务暂时不可用，请稍后重试。"


def on_tool_error(exc: Exception, request) -> str | None:
    """把工具异常转换成模型可读的安全错误。

    原始异常只写日志，不把文件路径、SQL、供应商响应或堆栈直接暴露给模型和用户。
    """
    tool_name = request.tool_call.get("name", "unknown")

    if isinstance(exc, GuardrailViolation):
        return exc.to_json()

    if isinstance(exc, (FileNotFoundError, PermissionError)):
        logger.exception("工具访问文件失败: tool=%s", tool_name)
        return json.dumps(
            {
                "ok": False,
                "error_code": "FILE_ERROR",
                "message": "文件访问失败，请检查路径或权限后重试。",
            },
            ensure_ascii=False,
        )

    if isinstance(exc, sqlite3.Error):
        logger.exception("数据库操作失败: tool=%s", tool_name)
        return json.dumps(
            {
                "ok": False,
                "error_code": "DATABASE_ERROR",
                "message": "数据库操作失败，请稍后重试。",
            },
            ensure_ascii=False,
        )

    logger.exception("工具执行异常: tool=%s", tool_name)
    return json.dumps(
        {
            "ok": False,
            "error_code": "TOOL_ERROR",
            "message": "工具执行失败，请稍后重试。",
        },
        ensure_ascii=False,
    )