"""查询上下文构建。

根据根表和元数据，决定模型可见的 JOIN、表和字段范围。
这里只负责构建候选上下文，不负责最终 QueryPlan 合法性校验。
"""
import json


def build_root_tables_text(meta: dict) -> str:
    """生成 ROUTE_PROMPT 使用的候选根表说明。"""
    lines: list[str] = []

    for item in meta.get("root_tables", []):
        table = item.get("table", "")
        description = item.get("description", "")
        lines.append(f"- {table}: {description}")

    return "\n".join(lines) if lines else "（无候选根表）"


def build_join_graph_text(join_edges: list[dict]) -> str:
    """把一组 JOIN 边渲染成模型可读文本。

    这里使用 <-> 表示无向关系。模型只需要选出连接当前已连接表的
    join_id，实际 SQL 中的 JOIN 方向由 resolve_joins 自动定向。
    """
    lines: list[str] = []

    for edge in join_edges:
        join_id = edge.get("join_id", "")
        left_table = edge.get("left_table", "")
        right_table = edge.get("right_table", "")
        description = edge.get("description", "")
        lines.append(
            f"- {join_id}: {left_table} <-> {right_table}，{description}"
        )

    return "\n".join(lines) if lines else "（无可用关联）"


def build_join_graph_overview(meta: dict) -> str:
    """生成路由阶段使用的全量 JOIN 关系概览。"""
    return build_join_graph_text(meta.get("join_graph", []))


def build_join_context(
    root_table: str,
    meta: dict,
    max_join_depth: int,
) -> tuple[list[dict], set[str]]:
    """从根表出发收集多跳可达的 JOIN 边和候选表。

    Args:
        root_table: 查询起点表。
        meta: query_meta 元数据，包含 join_graph。
        max_join_depth: 最大 JOIN 跳数。最多连接 4 张表时，
            最大跳数为 3。

    Returns:
        tuple:
            - 可达 JOIN 边列表，按 BFS 发现顺序去重；
            - 从根表出发可达的所有表集合。
    """
    adjacency: dict[str, list[dict]] = {}

    for edge in meta.get("join_graph", []):
        if not isinstance(edge, dict):
            raise ValueError(f"join_graph 中的边必须是字典: {edge!r}")

        join_id = edge.get("join_id")
        left_table = edge.get("left_table")
        right_table = edge.get("right_table")

        if not isinstance(join_id, str):
            raise ValueError(f"join_id 必须是字符串: {join_id!r}")

        if not isinstance(left_table, str) or not isinstance(right_table, str):
            raise ValueError(
                f"JOIN {join_id} 的表名必须是字符串，"
                f"当前为 {left_table!r} / {right_table!r}"
            )

        adjacency.setdefault(left_table, []).append(edge)
        adjacency.setdefault(right_table, []).append(edge)

    reachable_tables = {root_table}
    frontier = [root_table]
    discovered_edges: dict[str, dict] = {}

    for _ in range(max(0, max_join_depth)):
        if not frontier:
            break

        next_frontier: list[str] = []

        for table in frontier:
            for edge in adjacency.get(table, []):
                join_id = edge.get("join_id")
                left_table = edge.get("left_table")
                right_table = edge.get("right_table")

                if not isinstance(join_id, str):
                    raise ValueError(
                        f"BFS 中的 join_id 必须是字符串，"
                        f"当前类型为 {type(join_id).__name__}，值={join_id!r}"
                    )

                if not isinstance(left_table, str):
                    raise ValueError(
                        f"JOIN {join_id} 的 left_table 必须是字符串，"
                        f"当前类型为 {type(left_table).__name__}，值={left_table!r}"
                    )

                if not isinstance(right_table, str):
                    raise ValueError(
                        f"JOIN {join_id} 的 right_table 必须是字符串，"
                        f"当前类型为 {type(right_table).__name__}，值={right_table!r}"
                    )

                discovered_edges[join_id] = edge

                if left_table == table:
                    other_table = right_table
                else:
                    other_table = left_table

                if not isinstance(other_table, str):
                    raise ValueError(
                        f"JOIN {join_id} 计算出的 other_table 必须是字符串，"
                        f"当前类型为 {type(other_table).__name__}，值={other_table!r}"
                    )

                if other_table not in reachable_tables:
                    reachable_tables.add(other_table)
                    next_frontier.append(other_table)

        frontier = next_frontier

    return list(discovered_edges.values()), reachable_tables


def build_field_list_text(
    field_meta: dict,
    candidate_tables: set[str],
    enum_dict: dict | None = None,
) -> str:
    """生成模型可见的字段列表文本。

    Args:
        field_meta: 业务字段元数据，键格式为 表.字段。
        candidate_tables: 本次候选表集合。
        enum_dict: 枚举字段字典，用于给字段加 [枚举] 标记。

    Returns:
        按字段名排序的字段说明文本。
    """
    enum_dict = enum_dict or {}
    lines: list[str] = []

    for field_key in sorted(field_meta):
        table_name = field_key.split(".", 1)[0]
        if table_name not in candidate_tables:
            continue

        info = field_meta[field_key]
        biz_name = info.get("中文名称", field_key.split(".", 1)[-1])
        data_type = info.get("数据类型", "未知")
        meaning = info.get("业务含义", "").strip()
        enum_marker = " [枚举]" if field_key in enum_dict else ""

        line = f"- {field_key}: {biz_name}（{data_type}）{enum_marker}"
        if meaning:
            line += f" {meaning}"
        lines.append(line)

    return "\n".join(lines) if lines else "（无可用字段）"


def build_enum_dict_text(meta: dict, candidate_tables: set[str]) -> str:
    """生成候选表范围内使用的枚举字典 JSON 文本。"""
    enum_dict = {
        field_key: values
        for field_key, values in meta.get("enum_dict", {}).items()
        if field_key.split(".", 1)[0] in candidate_tables
    }
    return json.dumps(enum_dict, ensure_ascii=False)