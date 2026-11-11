
"""
查询计划 Schema（LLM 输出契约）+ 后端校验层
LLM 只做意图解析：选根表/选关联/选字段/翻译条件 → QueryPlan JSON
代码负责：白名单校验、枚举校验、JOIN 自动定向，全部通过才放行渲染
"""
import json
from pathlib import Path
from typing import  List, Literal, Optional, Union

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
QUERY_META_PATH = ROOT / "rules" / "metadata" / "query_meta.json"
CATALOG_PATH = ROOT / "rules" / "metadata" / "business_catalog.json"


# ========== 元数据加载 ==========
def load_query_meta() -> dict:
    with open(QUERY_META_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_field_meta() -> dict:
    """业务数据说明表 → {"表.列": {"中文名称":..., "数据类型":...}}"""
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    return catalog.get("fields", {})


# ========== QueryPlan Schema（递归条件树） ==========
class Predicate(BaseModel):
    """单条件节点：value 与 value_col 二选一（标量比较 / 字段比较）"""
    type: Literal["predicate"] = "predicate"
    table: str = Field(description="条件字段所属表名")
    col: str = Field(description="条件字段名")
    op: Literal["=", "!=", ">", "<", ">=", "<=", "LIKE", "IN"]
    value: Optional[Union[str, int, float, List[Union[str, int, float]]]] = Field(
        default=None, description="条件值：枚举字段必须使用数据库存储值；IN 时为列表"
    )
    value_col: Optional[str] = Field(
        default=None,
        description="字段与字段比较时的右值字段（格式：表.字段），如延迟判断 delivered_at > estimated_delivery_at"
    )


class LogicNode(BaseModel):
    """逻辑组合节点：AND/OR + 子节点数组（递归）"""
    type: Literal["logic"] = "logic"
    op: Literal["AND", "OR"]
    children: List[Union["Predicate", "LogicNode"]]


LogicNode.model_rebuild()

# 普通 Union：DashScope 的 OpenAI 兼容层不支持 discriminator 格式
WhereNode = Union[Predicate, LogicNode]

class ValueExpr(BaseModel):
    """通用标量表达式节点。kind 决定当前节点使用哪些字段。

    kind 取值：
    - field：普通字段，使用 table + col
    - aggregate：聚合表达式，使用 agg + table + col，可选 filter_ast 做条件聚合
    - literal：字面量，使用 value
    - binary：二元算术表达式，使用 left + op + right

    示例：
    延迟率 =
    {
        "kind": "binary",
        "op": "/",
        "left": {
            "kind": "aggregate",
            "agg": "COUNT_DISTINCT",
            "table": "orders",
            "col": "order_id",
            "filter_ast": {
                "type": "predicate",
                "table": "orders",
                "col": "delivered_at",
                "op": ">",
                "value": None,
                "value_col": "orders.estimated_delivery_at"
            }
        },
        "right": {
            "kind": "aggregate",
            "agg": "COUNT_DISTINCT",
            "table": "orders",
            "col": "order_id"
        }
    }
    """

    kind: Literal["field", "aggregate", "literal", "binary"]

    table: Optional[str] = Field(
        default=None,
        description="kind=field/aggregate 时必填：字段所属表名",
    )
    col: Optional[str] = Field(
        default=None,
        description="kind=field/aggregate 时必填：字段名；COUNT(*) 时填 *",
    )
    agg: Optional[
        Literal["SUM", "COUNT", "AVG", "MAX", "MIN", "COUNT_DISTINCT"]
    ] = Field(
        default=None,
        description="kind=aggregate 时必填：聚合函数",
    )
    filter_ast: Optional[WhereNode] = Field(
        default=None,
        description=(
            "条件聚合的过滤表达式，仅 kind=aggregate 使用。"
            "例如延迟订单数：COUNT_DISTINCT(orders.order_id) "
            "且过滤 delivered_at > estimated_delivery_at"
        ),
    )

    value: Optional[Union[str, int, float]] = Field(
        default=None,
        description="kind=literal 时必填：字面量",
    )

    left: Optional["ValueExpr"] = Field(
        default=None,
        description="kind=binary 时必填：左表达式",
    )
    right: Optional["ValueExpr"] = Field(
        default=None,
        description="kind=binary 时必填：右表达式",
    )
    op: Optional[Literal["+", "-", "*", "/"]] = Field(
        default=None,
        description="kind=binary 时必填：二元运算符",
    )


ValueExpr.model_rebuild()

class JoinChoice(BaseModel):
    """选择的关联连线：只能引用元数据中已存在的 join_id"""
    join_id: str

class SelectExpr(BaseModel):
    """一个输出列：<表达式> AS <别名>。

    别名是后续 HAVING 和 ORDER BY 的引用键，因此：
    1. alias 必须非空；
    2. 同一个 QueryPlan 内 alias 不能重复；
    3. 推荐使用业务中文名，如“延迟率”“送达订单数”。

    示例：
    {
        "alias": "延迟率",
        "expr": {
            "kind": "binary",
            "op": "/",
            "left": {
                "kind": "aggregate",
                "agg": "COUNT_DISTINCT",
                "table": "orders",
                "col": "order_id",
                "filter_ast": {
                    "type": "predicate",
                    "table": "orders",
                    "col": "delivered_at",
                    "op": ">",
                    "value": None,
                    "value_col": "orders.estimated_delivery_at"
                }
            },
            "right": {
                "kind": "aggregate",
                "agg": "COUNT_DISTINCT",
                "table": "orders",
                "col": "order_id"
            }
        }
    }
    """

    alias: str = Field(
        min_length=1,
        max_length=40,
        description="输出列别名；同一计划内必须唯一，HAVING/ORDER BY 通过它引用该列",
    )
    expr: ValueExpr = Field(
        description="输出列的标量表达式，可以是字段、聚合、字面量或二元表达式",
    )

class OrderByItem(BaseModel):
    """排序项：按某个输出列的别名排序。

    使用别名而不是重复表达式有两个好处：
    1. 计划结构更简单，模型不需要重复生成聚合表达式；
    2. 渲染器可直接生成 ORDER BY "别名" DESC。

    示例：
    {
        "select_alias": "延迟率",
        "direction": "DESC"
    }
    """

    select_alias: str = Field(
        min_length=1,
        max_length=40,
        description="要排序的输出列别名，必须存在于 select_exprs 中",
    )
    direction: Literal["ASC", "DESC"] = Field(
        default="DESC",
        description="排序方向：ASC 升序，DESC 降序；排名/最高/最低通常使用 DESC/ASC",
    )

class SelectCol(BaseModel):
    """输出列：col=* 且 agg=COUNT 表示 COUNT(*)"""
    table: str
    col: str
    agg: Optional[Literal["SUM", "COUNT", "AVG", "MAX", "MIN", "COUNT_DISTINCT"]] = None


class HavingCond(BaseModel):
    """聚合后过滤：通过输出列别名引用 select_exprs 中的聚合结果。"""

    select_alias: str = Field(
        min_length=1,
        max_length=40,
        description="要过滤的输出列别名，必须存在于 select_exprs 且包含聚合",
    )
    op: Literal[">=", ">", "=", "<", "<="] = Field(
        default=">=",
        description="比较运算符",
    )
    value: Union[int, float] = Field(
        description="HAVING 比较使用的数值",
    )


class RootTableChoice(BaseModel):
    """Step2 路由结果"""
    root_table: str = Field(description="从候选根表中选择的起点表")
    matched: bool = Field(description="候选根表能否覆盖用户问题")
    reason: str = Field(description="选择理由")


class QueryPlan(BaseModel):
    """Step6 输出的完整查询计划。

    查询计划由以下部分组成：
    1. root_table + join_list：确定查询的数据范围；
    2. where_ast：关联前的行级过滤；
    3. select_exprs：输出列，每列由 ValueExpr 和 alias 组成；
    4. group_by：分组字段，格式为 "表名.字段名"；
    5. having_ast：按输出列 alias 对聚合结果过滤；
    6. order_by：按输出列 alias 排序；
    7. count_subquery：对分组结果再计数；
    8. limit：最终返回行数上限。

    延迟率示例：
    {
        "root_table": "orders",
        "join_list": [
            {"join_id": "orders_order_items"},
            {"join_id": "order_items_sellers"}
        ],
        "select_exprs": [
            {
                "alias": "商家编号",
                "expr": {
                    "kind": "field",
                    "table": "order_items",
                    "col": "seller_id"
                }
            },
            {
                "alias": "送达订单数",
                "expr": {
                    "kind": "aggregate",
                    "agg": "COUNT_DISTINCT",
                    "table": "orders",
                    "col": "order_id"
                }
            },
            {
                "alias": "延迟订单数",
                "expr": {
                    "kind": "aggregate",
                    "agg": "COUNT_DISTINCT",
                    "table": "orders",
                    "col": "order_id",
                    "filter_ast": {
                        "type": "predicate",
                        "table": "orders",
                        "col": "delivered_at",
                        "op": ">",
                        "value": None,
                        "value_col": "orders.estimated_delivery_at"
                    }
                }
            },
            {
                "alias": "延迟率",
                "expr": {
                    "kind": "binary",
                    "op": "/",
                    "left": {
                        "kind": "aggregate",
                        "agg": "COUNT_DISTINCT",
                        "table": "orders",
                        "col": "order_id",
                        "filter_ast": {
                            "type": "predicate",
                            "table": "orders",
                            "col": "delivered_at",
                            "op": ">",
                            "value": None,
                            "value_col": "orders.estimated_delivery_at"
                        }
                    },
                    "right": {
                        "kind": "aggregate",
                        "agg": "COUNT_DISTINCT",
                        "table": "orders",
                        "col": "order_id"
                    }
                }
            }
        ],
        "group_by": ["order_items.seller_id"],
        "where_ast": {
            "type": "logic",
            "op": "AND",
            "children": [
                {
                    "type": "predicate",
                    "table": "orders",
                    "col": "order_status",
                    "op": "=",
                    "value": "delivered"
                },
                {
                    "type": "predicate",
                    "table": "orders",
                    "col": "purchase_at",
                    "op": ">=",
                    "value": "2024-05-01 00:00:00"
                },
                {
                    "type": "predicate",
                    "table": "orders",
                    "col": "purchase_at",
                    "op": "<=",
                    "value": "2024-05-31 23:59:59"
                }
            ]
        },
        "having_ast": [
            {
                "select_alias": "送达订单数",
                "op": ">=",
                "value": 10
            }
        ],
        "order_by": [
            {
                "select_alias": "延迟率",
                "direction": "DESC"
            }
        ],
        "count_subquery": false,
        "limit": 10
    }
    """

    root_table: str = Field(
        description="查询起点表，必须来自 query_meta.json 的 root_tables",
    )
    join_list: List[JoinChoice] = Field(
        default_factory=list,
        description="按拼接顺序排列的关联边，只能引用 query_meta.json 中已有的 join_id",
    )

    select_exprs: List[SelectExpr] = Field(
        min_length=1,
        description="输出列列表；每列必须带唯一 alias",
    )
    group_by: List[str] = Field(
        default_factory=list,
        description='分组字段列表，每项格式为 "表名.字段名"，例如 ["order_items.seller_id"]',
    )

    where_ast: Optional[WhereNode] = Field(
        default=None,
        description="行级过滤条件树；无条件时为 null",
    )
    having_ast: Optional[List[HavingCond]] = Field(
        default=None,
        description="聚合后过滤条件；每项通过 select_alias 引用 select_exprs 中的列",
    )
    order_by: List[OrderByItem] = Field(
        default_factory=list,
        description="排序列表；每项通过 select_alias 引用 select_exprs 中的列",
    )

    count_subquery: bool = Field(
        default=False,
        description="true 表示对分组结果再计数，例如复购客户数",
    )
    limit: Optional[int] = Field(
        default=100,
        ge=1,
        description="最终返回行数上限；null 表示不限制",
    )


# ========== 校验层（Step7） ==========
MAX_JOIN_TABLES = 4      # root + 3 张关联表
MAX_AST_DEPTH = 10       # 条件树最大嵌套深度
MAX_IN_VALUES = 20
MAX_LIKE_LEN = 50

ALLOWED_OPS = {"=", "!=", ">", "<", ">=", "<=", "LIKE", "IN"}


class PlanValidationError(ValueError):
    """校验失败，message 可直接喂回 LLM 重试"""

def _check_field_ref(
    table: Optional[str],
    col: Optional[str],
    connected: set[str],
    field_meta: dict,
    context: str,
) -> None:
    """校验一个字段引用是否合法。

    Args:
        table: 字段所属表名。
        col: 字段名；允许 "*"，用于 COUNT(*) 等聚合场景。
        connected: 当前查询已经连接的表集合。
        field_meta: 业务数据说明表中的字段元数据。
        context: 出错时的定位信息，例如 "select_exprs[0]"。

    Raises:
        PlanValidationError: 表未连接、字段缺失或字段不在白名单中。
    """
    if not table:
        raise PlanValidationError(f"{context} 缺少 table")

    if not col:
        raise PlanValidationError(f"{context} 缺少 col")

    if table not in connected:
        raise PlanValidationError(
            f"{context} 中的表 '{table}' 未连接，"
            f"当前已连接表: {sorted(connected)}"
        )

    if col == "*":
        return

    key = f"{table}.{col}"
    if key not in field_meta:
        available = sorted(
            field_key.split(".", 1)[1]
            for field_key in field_meta
            if field_key.startswith(f"{table}.")
        )
        raise PlanValidationError(
            f"{context} 的字段 '{key}' 不在业务数据说明表中。"
            f"'{table}' 的可用字段: {available}"
        )

def _collect_ast_nodes(node, depth=1):
    """递归收集条件树节点 + 深度检查"""
    if depth > MAX_AST_DEPTH:
        raise PlanValidationError(f"条件树嵌套超过 {MAX_AST_DEPTH} 层")
    if node.type == "predicate":
        yield node
    else:
        if not node.children:
            raise PlanValidationError("logic 节点的 children 不能为空")
        for child in node.children:
            yield from _collect_ast_nodes(child, depth + 1)

def _validate_where_ast(
    where_ast: Optional[WhereNode],
    connected: set[str],
    field_meta: dict,
    enum_dict: dict,
    context: str = "where_ast",
) -> None:
    """校验条件树中的字段、运算符、枚举值和字段比较。"""
    if where_ast is None:
        return

    for node in _collect_ast_nodes(where_ast):
        _check_field_ref(
            node.table,
            node.col,
            connected,
            field_meta,
            f"{context} 条件",
        )

        if node.op not in ALLOWED_OPS:
            raise PlanValidationError(
                f"{context} 中的运算符 '{node.op}' 不在允许列表: "
                f"{sorted(ALLOWED_OPS)}"
            )

        key = f"{node.table}.{node.col}"

        # 字段与字段比较
        if node.value_col is not None:
            if not isinstance(node.value_col, str) or "." not in node.value_col:
                raise PlanValidationError(
                    f"{context} 的 value_col '{node.value_col}' 格式应为 表.字段"
                )

            value_table, value_col = node.value_col.split(".", 1)
            _check_field_ref(
                value_table,
                value_col,
                connected,
                field_meta,
                f"{context} 的 value_col",
            )
            continue

        if node.value is None:
            raise PlanValidationError(
                f"{context} 的条件 '{key}' 缺少 value，"
                "或应使用 value_col 做字段比较"
            )

        # 枚举字段必须使用数据库存储值
        if key in enum_dict:
            allowed_values = enum_dict[key]
            values = node.value if isinstance(node.value, list) else [node.value]
            for value in values:
                if value not in allowed_values:
                    raise PlanValidationError(
                        f"{context} 中枚举字段 '{key}' 的值 '{value}' 不合法，"
                        f"允许值: {allowed_values}"
                    )

        if node.op == "LIKE":
            if not isinstance(node.value, str) or len(node.value) > MAX_LIKE_LEN:
                raise PlanValidationError(
                    f"{context} 中 LIKE 条件值必须是长度不超过 "
                    f"{MAX_LIKE_LEN} 的字符串"
                )

        if node.op == "IN":
            if not isinstance(node.value, list) or not (
                1 <= len(node.value) <= MAX_IN_VALUES
            ):
                raise PlanValidationError(
                    f"{context} 中 IN 条件值必须是非空列表，"
                    f"且最多 {MAX_IN_VALUES} 个元素"
                )

def _validate_value_expr(
    expr: ValueExpr,
    connected: set[str],
    field_meta: dict,
    enum_dict: dict,
    context: str = "expr",
) -> bool:
    """递归校验表达式，并返回该表达式是否包含聚合。

    返回 True 表示表达式包含聚合，可用于判断某个输出列能否被
    HAVING 或 ORDER BY 当作聚合结果引用。
    """
    if expr.kind == "field":
        _check_field_ref(
            expr.table,
            expr.col,
            connected,
            field_meta,
            context,
        )
        return False

    if expr.kind == "aggregate":
        if expr.agg is None:
            raise PlanValidationError(f"{context} 是聚合表达式，但缺少 agg")
        if not expr.table or not expr.col:
            raise PlanValidationError(
                f"{context} 是聚合表达式，但缺少 table 或 col"
            )

        if expr.col == "*" and expr.agg != "COUNT":
            raise PlanValidationError(
                f"{context} 中 col='*' 只支持 COUNT(*)，当前 agg='{expr.agg}'"
            )

        _check_field_ref(
            expr.table,
            expr.col,
            connected,
            field_meta,
            context,
        )

        if expr.filter_ast is not None:
            _validate_where_ast(
                expr.filter_ast,
                connected,
                field_meta,
                enum_dict,
                context=f"{context}.filter_ast",
            )

        return True

    if expr.kind == "literal":
        if expr.value is None:
            raise PlanValidationError(f"{context} 是字面量表达式，但缺少 value")
        return False

    if expr.kind == "binary":
        if expr.op is None:
            raise PlanValidationError(f"{context} 是二元表达式，但缺少 op")
        if expr.left is None or expr.right is None:
            raise PlanValidationError(
                f"{context} 是二元表达式，但缺少 left 或 right"
            )

        left_has_agg = _validate_value_expr(
            expr.left,
            connected,
            field_meta,
            enum_dict,
            context=f"{context}.left",
        )
        right_has_agg = _validate_value_expr(
            expr.right,
            connected,
            field_meta,
            enum_dict,
            context=f"{context}.right",
        )
        return left_has_agg or right_has_agg

    raise PlanValidationError(
        f"{context} 的表达式类型 '{expr.kind}' 不合法，"
        "只支持 field / aggregate / literal / binary"
    )

def _validate_select_exprs(
    select_exprs: list[SelectExpr],
    connected: set[str],
    field_meta: dict,
    enum_dict: dict,
) -> dict[str, bool]:
    """校验所有输出列，并返回 {输出列别名: 是否包含聚合}。"""
    if not select_exprs:
        raise PlanValidationError("select_exprs 不能为空")

    alias_has_aggregate: dict[str, bool] = {}
    seen_aliases: dict[str, str] = {}

    for index, item in enumerate(select_exprs):
        alias = item.alias

        if not alias or not alias.strip():
            raise PlanValidationError(
                f"select_exprs[{index}] 的 alias 不能为空"
            )

        if alias != alias.strip():
            raise PlanValidationError(
                f"select_exprs[{index}] 的 alias '{alias}' "
                "不能以空白字符开头或结尾"
            )

        if '"' in alias or any(ord(ch) < 32 for ch in alias):
            raise PlanValidationError(
                f"select_exprs[{index}] 的 alias '{alias}' "
                "包含不允许的引号或控制字符"
            )

        alias_key = alias.casefold()
        if alias_key in seen_aliases:
            raise PlanValidationError(
                f"select_exprs 中存在重复别名: "
                f"'{seen_aliases[alias_key]}' 和 '{alias}'"
            )
        seen_aliases[alias_key] = alias

        has_aggregate = _validate_value_expr(
            item.expr,
            connected,
            field_meta,
            enum_dict,
            context=f"select_exprs[{index}] '{alias}'",
        )
        alias_has_aggregate[alias] = has_aggregate

    return alias_has_aggregate

def _validate_group_by(
    group_by: list[str],
    select_exprs: list[SelectExpr],
    alias_has_aggregate: dict[str, bool],
    connected: set[str],
    field_meta: dict,
) -> None:
    """校验 GROUP BY 字段及聚合查询的分组完整性。"""

    def collect_field_keys(expr: ValueExpr) -> set[str]:
        """收集表达式中的普通字段引用。"""
        if expr.kind == "field":
            return {f"{expr.table}.{expr.col}"}
        if expr.kind == "binary":
            keys: set[str] = set()
            if expr.left is not None:
                keys.update(collect_field_keys(expr.left))
            if expr.right is not None:
                keys.update(collect_field_keys(expr.right))
            return keys
        return set()

    group_keys: set[str] = set()
    seen_group_keys: dict[str, str] = {}

    for index, field_key in enumerate(group_by):
        if not isinstance(field_key, str):
            raise PlanValidationError(
                f"group_by[{index}] 必须是字符串，格式为 表.字段"
            )

        if field_key != field_key.strip():
            raise PlanValidationError(
                f"group_by[{index}] '{field_key}' "
                "不能以空白字符开头或结尾"
            )

        if "." not in field_key:
            raise PlanValidationError(
                f"group_by[{index}] '{field_key}' 格式应为 表.字段"
            )

        table, col = field_key.split(".", 1)

        _check_field_ref(
            table,
            col,
            connected,
            field_meta,
            f"group_by[{index}]",
        )

        normalized_key = field_key.casefold()
        if normalized_key in seen_group_keys:
            raise PlanValidationError(
                f"group_by 中存在重复字段: "
                f"'{seen_group_keys[normalized_key]}' 和 '{field_key}'"
            )
        seen_group_keys[normalized_key] = field_key
        group_keys.add(field_key)

    has_any_aggregate = any(alias_has_aggregate.values())
    if not has_any_aggregate:
        return

    for index, item in enumerate(select_exprs):
        if alias_has_aggregate.get(item.alias, False):
            continue

        field_keys = collect_field_keys(item.expr)
        missing_keys = sorted(field_keys - group_keys)
        if missing_keys:
            raise PlanValidationError(
                f"select_exprs[{index}] '{item.alias}' 包含未分组字段: "
                f"{missing_keys}。存在聚合输出时，所有非聚合字段都必须"
                "出现在 group_by 中"
            )

def _validate_output_references(
    having_ast: Optional[list[HavingCond]],
    order_by: list[OrderByItem],
    alias_has_aggregate: dict[str, bool],
) -> None:
    """校验 HAVING 和 ORDER BY 对输出列别名的引用。"""
    available_aliases = sorted(alias_has_aggregate)

    for index, condition in enumerate(having_ast or []):
        alias = condition.select_alias

        if alias not in alias_has_aggregate:
            raise PlanValidationError(
                f"having_ast[{index}] 引用了不存在的输出列别名 "
                f"'{alias}'，可用别名: {available_aliases}"
            )

        if not alias_has_aggregate[alias]:
            raise PlanValidationError(
                f"having_ast[{index}] 引用的输出列 '{alias}' "
                "不包含聚合，不能用于 HAVING"
            )

        if isinstance(condition.value, bool) or not isinstance(
            condition.value, (int, float)
        ):
            raise PlanValidationError(
                f"having_ast[{index}] 的比较值必须是数字，"
                f"当前为 {type(condition.value).__name__}"
            )

    for index, item in enumerate(order_by):
        alias = item.select_alias

        if alias not in alias_has_aggregate:
            raise PlanValidationError(
                f"order_by[{index}] 引用了不存在的输出列别名 "
                f"'{alias}'，可用别名: {available_aliases}"
            )

        if item.direction not in {"ASC", "DESC"}:
            raise PlanValidationError(
                f"order_by[{index}] 的排序方向 '{item.direction}' 不合法，"
                "只支持 ASC 或 DESC"
            )

def validate_plan(plan: QueryPlan, meta: dict, field_meta: dict) -> tuple:
    """校验完整查询计划。

    Returns:
        tuple: (已连接表列表, 定向后的 JOIN 列表)

    Raises:
        PlanValidationError: 任意部分不合法。
    """
    valid_root_tables = {item["table"] for item in meta["root_tables"]}
    if plan.root_table not in valid_root_tables:
        raise PlanValidationError(
            f"root_table '{plan.root_table}' 不在候选根表中，"
            f"可用根表: {sorted(valid_root_tables)}"
        )

    enum_dict = meta.get("enum_dict", {})

    resolved_joins, connected_list = resolve_joins(plan, meta)
    connected = set(connected_list)

    alias_has_aggregate = _validate_select_exprs(
        plan.select_exprs,
        connected,
        field_meta,
        enum_dict,
    )

    _validate_group_by(
        plan.group_by,
        plan.select_exprs,
        alias_has_aggregate,
        connected,
        field_meta,
    )

    _validate_where_ast(
        plan.where_ast,
        connected,
        field_meta,
        enum_dict,
        context="where_ast",
    )

    _validate_output_references(
        plan.having_ast,
        plan.order_by,
        alias_has_aggregate,
    )

    _validate_count_subquery_semantics(plan)

    if plan.limit is not None and plan.limit < 1:
        raise PlanValidationError("limit 必须大于等于 1")

    return sorted(connected), resolved_joins

def _validate_count_subquery_semantics(plan: QueryPlan) -> None:
    """校验 count_subquery 的语义。

    特别拦截：group_by 某个字段后，又对同一个字段做聚合并用它 HAVING。
    这种写法通常无法表达“至少 N 笔订单/记录”，例如：
        GROUP BY customers.customer_unique_id
        HAVING COUNT(DISTINCT customers.customer_unique_id) >= 2
    """
    if not plan.count_subquery:
        return

    if not plan.group_by:
        raise PlanValidationError(
            "count_subquery=true 时必须提供 group_by，"
            "否则不是对分组结果再计数"
        )

    group_fields = set(plan.group_by)
    select_by_alias = {
        item.alias: item.expr
        for item in plan.select_exprs
    }

    def collect_aggregate_terms(expr: ValueExpr):
        """递归收集表达式中的聚合项。"""
        if expr.kind == "aggregate":
            yield expr
            return

        if expr.kind == "binary":
            if expr.left is not None:
                yield from collect_aggregate_terms(expr.left)
            if expr.right is not None:
                yield from collect_aggregate_terms(expr.right)

    for index, condition in enumerate(plan.having_ast or []):
        expr = select_by_alias.get(condition.select_alias)
        if expr is None:
            continue

        for aggregate in collect_aggregate_terms(expr):
            if not aggregate.table or not aggregate.col:
                continue

            field_key = f"{aggregate.table}.{aggregate.col}"
            if field_key in group_fields:
                raise PlanValidationError(
                    f"count_subquery=true 时，having_ast[{index}] "
                    f"引用的输出列 '{condition.select_alias}' "
                    f"对分组字段 '{field_key}' 自身做聚合，"
                    "无法表达“至少 N 笔订单/记录”。"
                    "请对订单编号等非分组字段使用 COUNT_DISTINCT，"
                    "并用该聚合别名做 HAVING。"
                )

def resolve_joins(plan: QueryPlan, meta: dict):
    """
    解析 JOIN 方向：元数据登记的是无向关系，运行时按根表起点自动定向。
    - 已连接侧是 left → 正向 JOIN right_table
    - 已连接侧是 right → 自动翻转，JOIN left_table
    - LEFT JOIN 翻转后等价于 RIGHT JOIN（SQLite 3.39+ 支持）
    返回 (resolved_joins, connected)：前者供渲染器拼接，后者供字段校验
    """
    join_meta = {j["join_id"]: j for j in meta["join_graph"]}
    connected = {plan.root_table}
    resolved = []
    for join in plan.join_list:
        j = join_meta.get(join.join_id)
        if j is None:
            raise PlanValidationError(
                f"join_id '{join.join_id}' 不存在，可用: {sorted(join_meta)}"
            )
        if j["left_table"] in connected:
            new_table = j["right_table"]
            join_type = j["join_type"]
        elif j["right_table"] in connected:
            new_table = j["left_table"]   # 自动翻转：另一端成为被 JOIN 的表
            join_type = (
                "RIGHT JOIN" if j["join_type"].upper() == "LEFT JOIN" else j["join_type"]
            )
        else:
            raise PlanValidationError(
                f"JOIN '{join.join_id}' 两端（{j['left_table']}/{j['right_table']}）"
                f"均未连接，当前已连接: {sorted(connected)}，请调整 join_list 顺序"
            )
        connected.add(new_table)
        resolved.append({
            "right_table": new_table,
            "join_type": join_type,
            "on_expr": j["on_expr"],
        })
    if len(connected) > MAX_JOIN_TABLES:
        raise PlanValidationError(f"关联表数量超过上限 {MAX_JOIN_TABLES}")
    return resolved, sorted(connected)

