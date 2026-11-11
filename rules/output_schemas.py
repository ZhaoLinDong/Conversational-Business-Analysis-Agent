"""
智能体输出契约定义
- BusinessAgentAnswer：模型最终回答的结构化格式（response_format）
- AgentRunResult：一次对话运行的完整结果（智能体与 GUI 之间的数据信封）
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

class ChartSpec(BaseModel):
    """结构化图表规格。

    图表数据直接使用 BusinessAgentAnswer.rows。
    x_field 和 y_field 必须是 rows 中真实存在的字段名。
    """

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "pie"] = Field(
        description=(
            "图表类型：bar 柱状图、line 折线图、pie 饼图。"
            "分类排名通常用 bar，时间趋势通常用 line，占比通常用 pie。"
        )
    )
    title: str = Field(
        min_length=1,
        description="图表标题",
    )
    x_field: str = Field(
        min_length=1,
        description="rows 中作为横轴或分类标签的字段名，必须精确匹配",
    )
    y_field: str = Field(
        min_length=1,
        description="rows 中作为数值轴的字段名，必须精确匹配",
    )
    x_label: Optional[str] = Field(
        default=None,
        description="横轴展示名称；不填时使用 x_field",
    )
    y_label: Optional[str] = Field(
        default=None,
        description="纵轴展示名称；不填时使用 y_field",
    )

class BusinessAgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        description=(
            "面向业务人员的最终回答，必须基于工具实际返回的数据。"
            "延迟率、占比、比率等百分比结果保留两位小数并四舍五入。"
        )
    )
    explanation: list[str] = Field(
        default_factory=list,
        description=(
            "结果解释，建议 2 到 4 条。说明结论依据、指标口径、"
            "时间范围、关键差异、限制或假设。只能引用工具实际返回的数据。"
        ),
    )

    answer_type: Literal[
        "business_query",
        "data_import",
        "general",
        "error",
    ] = Field(
        description="回答类型：业务查询、数据导入汇报、普通回答或错误说明。"
    )

    metric_id: Optional[str] = Field(
        default=None,
        description="本次回答使用的标准指标编码，例如 product_sales_amount。",
    )
    metric_name: Optional[str] = Field(
        default=None,
        description="本次回答使用的标准指标中文名称。",
    )
    time_range: Optional[str] = Field(
        default=None,
        description="查询时间范围，例如 2025-04-01 至 2025-04-30。",
    )
    dimensions: list[str] = Field(
        default_factory=list,
        description="本次查询使用的分析维度，例如 month、category、seller。",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="工具实际返回的数据行，不得编造或补全。",
    )

    caveats: list[str] = Field(
        default_factory=list,
        description="业务口径、数据质量或使用限制说明。",
    )


@dataclass
class AgentRunResult:
    """一次对话运行的完整结果"""
    status: str                    # completed / interrupted
    content: str = ""              # 模型的文本回复
    structured_response: Optional[BusinessAgentAnswer] = None
    interrupts: list[dict[str, Any]] = field(default_factory=list)  # 待人工审核的请求