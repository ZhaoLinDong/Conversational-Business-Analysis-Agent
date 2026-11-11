"""业务指标结构化规则契约。

指标规则只描述业务语义和 QueryPlan 模板，不包含自由 SQL。
最终执行前统一编译为 QueryPlan，并复用现有校验器和 SQL Renderer。
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rules.query_plan import SelectExpr, OrderByItem


class MetricValueBinding(BaseModel):
    """指标规则中的值来源。

    kind=literal：
        使用规则中配置的固定值，例如 delivered。

    kind=param：
        引用运行时参数名，例如 min_order_count。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["literal", "param"] = Field(
        default="literal",
        description="值来源：literal 为固定值，param 为运行时参数",
    )
    value: Any = Field(
        description=(
            "kind=literal 时为字面量；"
            "kind=param 时为参数名，例如 min_order_count"
        )
    )

    @model_validator(mode="after")
    def validate_param_name(self):
        """当 kind=param 时，确保 value 是非空参数名字符串。"""
        if self.kind == "param":
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError(
                    "MetricValueBinding.kind=param 时，"
                    "value 必须是非空参数名字符串"
                )
        return self

class MetricParameterSpec(BaseModel):
    """指标运行时参数定义。

    例如复购客户数指标可以定义：
        min_order_count:
            data_type = integer
            required = false
            default = 2
    """

    model_config = ConfigDict(extra="forbid")

    data_type: Literal[
        "string",
        "integer",
        "number",
        "boolean",
        "date",
    ] = Field(
        default="string",
        description="参数数据类型",
    )
    required: bool = Field(
        default=False,
        description="是否为必填参数",
    )
    default: Any = Field(
        default=None,
        description="参数默认值；required=true 时通常不设置",
    )
    allowed_values: list[Any] | None = Field(
        default=None,
        description="允许的参数值列表；null 表示不限制",
    )
    description: str = Field(
        default="",
        description="参数业务说明",
    )

    @model_validator(mode="after")
    def validate_default_value(self):
        """校验默认值是否满足允许值限制。"""
        if (
            self.default is not None
            and self.allowed_values is not None
            and self.default not in self.allowed_values
        ):
            raise ValueError(
                f"默认值 {self.default!r} 不在 allowed_values "
                f"{self.allowed_values!r} 中"
            )
        return self


class MetricFilterSpec(BaseModel):
    """指标过滤条件模板。

    示例：固定过滤 delivered：
        field = orders.order_status
        op = =
        value = MetricValueBinding(kind=literal, value=delivered)

    示例：字段比较：
        field = orders.delivered_at
        op = >
        value_col = orders.estimated_delivery_at
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="过滤字段，格式为 表.字段",
    )
    op: Literal[
        "=",
        "!=",
        ">",
        "<",
        ">=",
        "<=",
        "LIKE",
        "IN",
    ] = Field(
        description="比较运算符",
    )
    value: MetricValueBinding | None = Field(
        default=None,
        description="过滤值；与 value_col 二选一",
    )
    value_col: str | None = Field(
        default=None,
        description="字段与字段比较时的右值字段，格式为 表.字段",
    )
    description: str = Field(
        default="",
        description="过滤条件业务说明",
    )

    @model_validator(mode="after")
    def validate_filter_shape(self):
        """校验字段格式和 value/value_col 二选一。"""
        if "." not in self.field:
            raise ValueError(
                f"MetricFilterSpec.field '{self.field}' 格式应为 表.字段"
            )

        if self.value_col is not None and "." not in self.value_col:
            raise ValueError(
                f"MetricFilterSpec.value_col '{self.value_col}' "
                "格式应为 表.字段"
            )

        has_value = self.value is not None
        has_value_col = self.value_col is not None

        if has_value == has_value_col:
            raise ValueError(
                "MetricFilterSpec 必须且只能设置 value 或 value_col 其中之一"
            )

        return self


class MetricTimeSpec(BaseModel):
    """指标时间语义定义。

    示例：
        field = orders.purchase_at
        required = false
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="时间字段，格式为 表.字段，例如 orders.purchase_at",
    )
    required: bool = Field(
        default=False,
        description="是否必须提供时间范围",
    )
    description: str = Field(
        default="",
        description="时间口径业务说明",
    )

    @model_validator(mode="after")
    def validate_field_format(self):
        """校验时间字段格式。"""
        if "." not in self.field:
            raise ValueError(
                f"MetricTimeSpec.field '{self.field}' 格式应为 表.字段"
            )
        return self

class MetricDimensionSpec(BaseModel):
    """指标维度定义。

    以商家维度为例：
        dimension_id = seller
        name = 商家
        select_exprs:
            - seller_id  AS 商家编号
            - seller_name AS 商家名称
        group_by:
            - order_items.seller_id
            - sellers.seller_name
    """

    model_config = ConfigDict(extra="forbid")

    dimension_id: str = Field(
        min_length=1,
        description="维度唯一标识，例如 seller、category、customer",
    )
    name: str = Field(
        min_length=1,
        description="维度中文名称",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="维度业务别名，用于搜索匹配",
    )
    select_exprs: list[SelectExpr] = Field(
        min_length=1,
        description="选择该维度时需要加入 SELECT 的输出列",
    )
    group_by: list[str] = Field(
        min_length=1,
        description='GROUP BY 字段列表，格式为 "表.字段"',
    )
    description: str = Field(
        default="",
        description="维度业务说明",
    )

    @model_validator(mode="after")
    def validate_dimension_shape(self):
        """校验维度标识和 GROUP BY 字段格式。"""
        if not self.dimension_id.strip():
            raise ValueError("MetricDimensionSpec.dimension_id 不能为空")

        for field_key in self.group_by:
            if "." not in field_key:
                raise ValueError(
                    f"维度 '{self.dimension_id}' 的 group_by 字段 "
                    f"'{field_key}' 格式应为 表.字段"
                )

        return self

class MetricHavingSpec(BaseModel):
    """指标 HAVING 模板。

    示例：复购客户数指标
        select_alias = 已送达订单数
        op = >=
        value = MetricValueBinding(kind=param, value=min_order_count)
    """

    model_config = ConfigDict(extra="forbid")

    select_alias: str = Field(
        min_length=1,
        description="HAVING 引用的输出列别名，必须存在于指标输出列中",
    )
    op: Literal[">=", ">", "=", "<", "<="] = Field(
        default=">=",
        description="比较运算符",
    )
    value: MetricValueBinding = Field(
        description="HAVING 比较值，可以是固定值或运行时参数",
    )
    description: str = Field(
        default="",
        description="HAVING 条件业务说明",
    )

    @model_validator(mode="after")
    def validate_alias(self):
        """校验输出列别名非空。"""
        if not self.select_alias.strip():
            raise ValueError("MetricHavingSpec.select_alias 不能为空")
        return self

class MetricDefinition(BaseModel):
    """业务指标结构化定义。

    一个 MetricDefinition 包含：
    - 指标标识、名称、别名和说明；
    - 基表、固定 JOIN、固定过滤条件；
    - 时间字段；
    - 固定输出表达式和 GROUP BY；
    - 可选维度；
    - 可选 HAVING、排序和 limit；
    - 指标参数定义。

    指标定义不保存自由 SQL，只保存 QueryPlan 模板。
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(
        min_length=1,
        description="指标唯一标识，例如 repeat_customer_count",
    )
    name: str = Field(
        min_length=1,
        description="指标中文名称",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="指标业务别名，用于搜索匹配",
    )
    description: str = Field(
        default="",
        description="指标业务定义和口径说明",
    )
    version: str = Field(
        default="1.0",
        description="指标规则版本",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="典型用户问题示例，仅用于检索匹配，不作为硬编码答案",
    )

    base_table: str = Field(
        min_length=1,
        description="指标查询的起点表",
    )
    joins: list[str] = Field(
        default_factory=list,
        description="指标所需的 join_id 列表，按顺序排列",
    )
    parameters: dict[str, MetricParameterSpec] = Field(
        default_factory=dict,
        description="指标运行时参数字典，键为参数名",
    )

    filters: list[MetricFilterSpec] = Field(
        default_factory=list,
        description="指标固定过滤条件模板",
    )
    time: MetricTimeSpec | None = Field(
        default=None,
        description="指标时间语义；无时间范围要求时可省略",
    )

    select_exprs: list[SelectExpr] = Field(
        min_length=1,
        description="指标固定输出列和聚合表达式",
    )
    group_by: list[str] = Field(
        default_factory=list,
        description='固定 GROUP BY 字段列表，格式为 "表.字段"',
    )
    dimensions: list[MetricDimensionSpec] = Field(
        default_factory=list,
        description="指标支持的可选维度",
    )

    having: list[MetricHavingSpec] = Field(
        default_factory=list,
        description="指标固定 HAVING 条件模板",
    )
    order_by: list[OrderByItem] = Field(
        default_factory=list,
        description="指标默认排序",
    )
    count_subquery: bool = Field(
        default=False,
        description="是否对分组结果再计数",
    )
    default_limit: int = Field(
        default=100,
        ge=1,
        description="指标默认返回行数上限",
    )

    @model_validator(mode="after")
    def validate_metric_definition(self):
        """校验指标定义的内部一致性。"""
        if not self.metric_id.strip():
            raise ValueError("MetricDefinition.metric_id 不能为空")

        if not self.base_table.strip():
            raise ValueError("MetricDefinition.base_table 不能为空")

        if len(set(self.joins)) != len(self.joins):
            raise ValueError("MetricDefinition.joins 中存在重复 join_id")

        for field_key in self.group_by:
            if "." not in field_key:
                raise ValueError(
                    f"固定 GROUP BY 字段 '{field_key}' 格式应为 表.字段"
                )

        for parameter_name in self.parameters:
            if not parameter_name.strip():
                raise ValueError(
                    "MetricDefinition.parameters 的参数名不能为空"
                )

        select_aliases = [item.alias for item in self.select_exprs]
        all_aliases = list(select_aliases)

        dimension_ids: set[str] = set()
        for dimension in self.dimensions:
            if dimension.dimension_id in dimension_ids:
                raise ValueError(
                    f"MetricDefinition 中存在重复维度: "
                    f"{dimension.dimension_id}"
                )
            dimension_ids.add(dimension.dimension_id)
            all_aliases.extend(
                item.alias for item in dimension.select_exprs
            )

        normalized_aliases: set[str] = set()
        for alias in all_aliases:
            alias_key = alias.casefold()
            if alias_key in normalized_aliases:
                raise ValueError(
                    f"MetricDefinition 中存在重复输出列别名: {alias}"
                )
            normalized_aliases.add(alias_key)

        for index, condition in enumerate(self.having):
            if condition.select_alias not in select_aliases:
                raise ValueError(
                    f"having[{index}] 引用的输出列别名 "
                    f"'{condition.select_alias}' 不存在。"
                    f"可用固定输出列: {select_aliases}"
                )

        for index, item in enumerate(self.order_by):
            if item.select_alias.casefold() not in normalized_aliases:
                raise ValueError(
                    f"order_by[{index}] 引用的输出列别名 "
                    f"'{item.select_alias}' 不存在"
                )

        if self.count_subquery and not self.group_by:
            raise ValueError(
                "count_subquery=true 时，MetricDefinition.group_by 不能为空"
            )

        return self


class MetricTimeRange(BaseModel):
    """指标查询时间范围。

    支持两种输入格式：
    - YYYY-MM-DD
    - YYYY-MM-DD HH:MM:SS

    日期型 start 会补为 00:00:00，日期型 end 会补为 23:59:59。
    """

    model_config = ConfigDict(extra="forbid")

    start: str = Field(
        description="开始时间，格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )
    end: str = Field(
        description="结束时间，格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )

    @model_validator(mode="after")
    def validate_and_normalize_range(self):
        """校验时间格式、时区和先后顺序，并统一为数据库时间格式。"""

        def normalize(value: str, is_end: bool) -> str:
            raw = value.strip()
            if not raw:
                raise ValueError("时间范围不能为空")

            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError(
                    f"时间 '{value}' 格式无效，应为 YYYY-MM-DD "
                    "或 YYYY-MM-DD HH:MM:SS"
                ) from exc

            if parsed.tzinfo is not None:
                raise ValueError(
                    f"时间 '{value}' 不能包含时区信息"
                )

            # 纯日期输入根据起止位置补齐整天范围
            if len(raw) == 10:
                if is_end:
                    parsed = parsed.replace(
                        hour=23,
                        minute=59,
                        second=59,
                    )
                else:
                    parsed = parsed.replace(
                        hour=0,
                        minute=0,
                        second=0,
                    )

            return parsed.strftime("%Y-%m-%d %H:%M:%S")

        normalized_start = normalize(self.start, is_end=False)
        normalized_end = normalize(self.end, is_end=True)

        if normalized_start > normalized_end:
            raise ValueError(
                f"时间范围开始时间 {normalized_start} "
                f"不能晚于结束时间 {normalized_end}"
            )

        self.start = normalized_start
        self.end = normalized_end
        return self

class MetricQueryRequest(BaseModel):
    """一次指标查询请求。

    示例：
    {
        "metric_id": "repeat_customer_count",
        "time_range": null,
        "dimensions": [],
        "parameters": {
            "min_order_count": 2
        },
        "limit": 100
    }
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(
        min_length=1,
        description="目标指标唯一标识",
    )
    time_range: MetricTimeRange | None = Field(
        default=None,
        description="查询时间范围；指标允许不传时可省略",
    )
    dimensions: list[str] = Field(
        default_factory=list,
        description="请求使用的维度标识列表，例如 seller、category",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="指标运行时参数值，键为参数名",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="本次请求返回行数上限；null 表示使用指标默认值",
    )

    @model_validator(mode="after")
    def validate_query_request(self):
        """校验指标 ID、维度列表和请求一致性。"""
        if not self.metric_id.strip():
            raise ValueError("MetricQueryRequest.metric_id 不能为空")

        normalized_dimensions: set[str] = set()
        for dimension_id in self.dimensions:
            if not dimension_id.strip():
                raise ValueError(
                    "MetricQueryRequest.dimensions 不能包含空维度名"
                )

            dimension_key = dimension_id.casefold()
            if dimension_key in normalized_dimensions:
                raise ValueError(
                    f"MetricQueryRequest.dimensions 中存在重复维度: "
                    f"{dimension_id}"
                )
            normalized_dimensions.add(dimension_key)

        for parameter_name in self.parameters:
            if not parameter_name.strip():
                raise ValueError(
                    "MetricQueryRequest.parameters 不能包含空参数名"
                )

        return self