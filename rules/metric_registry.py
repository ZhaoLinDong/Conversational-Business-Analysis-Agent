"""业务指标规则加载与检索。"""
import json
from pathlib import Path

from rules.metric_schema import MetricDefinition


METRICS_DIR = (
    Path(__file__).resolve().parent
    / "metadata"
    / "metrics"
)


def load_metric_definitions(
    metrics_dir: Path | None = None,
) -> dict[str, MetricDefinition]:
    """加载目录中的全部指标定义。

    Args:
        metrics_dir: 指标 JSON 目录；不传时使用 rules/metadata/metrics。

    Returns:
        指标定义字典，键为 metric_id。

    Raises:
        FileNotFoundError: 指标目录不存在。
        ValueError: JSON 无效、契约校验失败或 metric_id 重复。
    """
    directory = metrics_dir or METRICS_DIR

    if not directory.exists():
        raise FileNotFoundError(
            f"指标规则目录不存在: {directory}"
        )

    if not directory.is_dir():
        raise NotADirectoryError(
            f"指标规则路径不是目录: {directory}"
        )

    metrics: dict[str, MetricDefinition] = {}
    seen_metric_ids: dict[str, Path] = {}

    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"指标规则 JSON 解析失败: {path}: {exc}"
            ) from exc

        try:
            metric = MetricDefinition.model_validate(payload)
        except ValueError as exc:
            raise ValueError(
                f"指标规则校验失败: {path}: {exc}"
            ) from exc

        if path.stem != metric.metric_id:
            raise ValueError(
                f"指标规则文件名必须与 metric_id 一致: "
                f"{path.name} != {metric.metric_id}.json"
            )

        metric_key = metric.metric_id.casefold()
        if metric_key in seen_metric_ids:
            raise ValueError(
                f"指标 metric_id 重复: {metric.metric_id}，"
                f"文件: {seen_metric_ids[metric_key]} 和 {path}"
            )

        seen_metric_ids[metric_key] = path
        metrics[metric.metric_id] = metric

    return metrics


def search_metric_definitions(
    query: str,
    metrics: dict[str, MetricDefinition] | None = None,
    limit: int = 5,
    min_score: float = 0.05,
) -> list[dict]:
    """搜索匹配的业务指标。

    Args:
        query: 用户问题或业务关键词。
        metrics: 已加载的指标字典；不传时从默认目录加载。
        limit: 最多返回候选数。
        min_score: 最低相关度阈值。

    Returns:
        轻量指标卡片列表，按相关度降序排列。
    """
    if limit < 1:
        raise ValueError("limit 必须大于等于 1")

    if not query or not query.strip():
        return []

    metric_map = metrics or load_metric_definitions()

    def normalize(text: str) -> str:
        """保留中文、英文和数字，去掉空白和标点。"""
        return "".join(
            ch.casefold()
            for ch in str(text)
            if ch.isalnum()
        )

    def bigrams(text: str) -> set[str]:
        """生成字符二元组。"""
        if len(text) < 2:
            return {text} if text else set()
        return {
            text[index:index + 2]
            for index in range(len(text) - 1)
        }

    def text_score(
        query_text: str,
        query_grams: set[str],
        candidate_text: str,
    ) -> float:
        """计算问题与单个文本字段的相关度。"""
        candidate = normalize(candidate_text)
        if not candidate:
            return 0.0

        if query_text == candidate:
            return 2.0

        score = 0.0
        if query_text in candidate or candidate in query_text:
            score += 1.0

        candidate_grams = bigrams(candidate)
        if query_grams and candidate_grams:
            overlap = len(query_grams & candidate_grams)
            score += (
                2.0
                * overlap
                / (len(query_grams) + len(candidate_grams))
            )

        return score

    query_text = normalize(query)
    if not query_text:
        return []

    query_grams = bigrams(query_text)
    results: list[dict] = []

    for metric in metric_map.values():
        field_scores: list[float] = [
            text_score(query_text, query_grams, metric.metric_id) * 1.5,
            text_score(query_text, query_grams, metric.name) * 3.0,
            text_score(
                query_text,
                query_grams,
                metric.description,
            ) * 1.5,
        ]

        for alias in metric.aliases:
            field_scores.append(
                text_score(query_text, query_grams, alias) * 3.5
            )

        for example in metric.examples:
            field_scores.append(
                text_score(query_text, query_grams, example) * 2.0
            )

        score = max(field_scores, default=0.0)
        if score < min_score:
            continue

        supports_overall = not (
            bool(metric.group_by)
            and not metric.count_subquery
        )

        results.append(
            {
                "metric_id": metric.metric_id,
                "name": metric.name,
                "aliases": metric.aliases,
                "description": metric.description,
                "version": metric.version,
                "score": round(score, 4),
                "parameters": {
                    parameter_name: {
                        "data_type": parameter.data_type,
                        "required": parameter.required,
                        "default": parameter.default,
                        "allowed_values": parameter.allowed_values,
                        "description": parameter.description,
                    }
                    for parameter_name, parameter in metric.parameters.items()
                },
                "dimensions": [
                    {
                        "dimension_id": dimension.dimension_id,
                        "name": dimension.name,
                        "aliases": dimension.aliases,
                        "description": dimension.description,
                    }
                    for dimension in metric.dimensions
                ],
                "supports_overall": supports_overall,
                "result_grain": (
                    "overall"
                    if supports_overall
                    else "grouped"
                ),
                "group_by": metric.group_by,
                "count_subquery": metric.count_subquery,
            }
        )

    results.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item["metric_id"]),
        )
    )

    return results[:limit]

def get_metric_definition(
    metric_id: str,
    metrics: dict[str, MetricDefinition] | None = None,
) -> MetricDefinition:
    """根据 metric_id 获取完整指标定义。

    Args:
        metric_id: 指标唯一标识。
        metrics: 已加载的指标字典；不传时从默认目录加载。

    Returns:
        MetricDefinition 对象。

    Raises:
        ValueError: metric_id 为空。
        KeyError: 指标不存在。
    """
    if not metric_id or not metric_id.strip():
        raise ValueError("metric_id 不能为空")

    metric_map = (
        metrics
        if metrics is not None
        else load_metric_definitions()
    )

    exact = metric_map.get(metric_id)
    if exact is not None:
        return exact

    normalized_metric_id = metric_id.casefold()
    for current_metric_id, metric in metric_map.items():
        if current_metric_id.casefold() == normalized_metric_id:
            return metric

    available = sorted(metric_map)
    raise KeyError(
        f"指标 '{metric_id}' 不存在。"
        f"可用指标: {available}"
    )