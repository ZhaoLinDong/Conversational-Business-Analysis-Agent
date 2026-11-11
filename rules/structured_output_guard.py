"""结构化模型输出解析与护栏。

模型通过 with_structured_output(include_raw=True) 返回结果后，
统一在这里提取 parsed，并记录不可解析的原始响应。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_structured_result(
    result: Any,
    label: str,
) -> tuple[Any, str | None]:
    """解析 include_raw=True 的结构化输出结果。

    Args:
        result: with_structured_output(include_raw=True) 的返回对象。
        label: 日志和错误信息中的步骤名称。

    Returns:
        tuple:
            - 成功时为解析后的 Pydantic 对象，失败时为 None；
            - 成功时为 None，失败时为可读错误信息。
    """
    if not isinstance(result, dict) or "parsed" not in result:
        return result, None

    parsed = result.get("parsed")
    parsing_error = result.get("parsing_error")

    if parsed is not None:
        return parsed, None

    raw = result.get("raw")
    raw_content = getattr(raw, "content", None)
    tool_calls = getattr(raw, "tool_calls", None)
    additional_kwargs = getattr(raw, "additional_kwargs", None)

    logger.warning(
        "%s 未返回可解析的结构化结果: "
        "parsing_error=%r, tool_calls=%r, "
        "raw_content=%r, additional_kwargs=%r",
        label,
        parsing_error,
        tool_calls,
        str(raw_content)[:2000],
        str(additional_kwargs)[:2000],
    )

    error_message = f"{label}未返回可解析的结构化结果"
    if parsing_error is not None:
        error_message += f"：{type(parsing_error).__name__}"

    return None, error_message