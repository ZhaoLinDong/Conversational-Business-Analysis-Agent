from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
EXCEL_PATH = ROOT / "docs" / "业务数据说明表.xlsx"
CATALOG_PATH = ROOT / "metadata" / "business_catalog.json"

SHEET_SPECS = {
    "tables": {
        "sheet": "01_表级别",
        "required": ["表名", "中文名称", "所属模块", "表类型", "数据量级", "更新频率"],
    },
    "fields": {
        "sheet": "02_字段级别",
        "required": ["字段名", "中文名称", "数据类型", "取值范围", "业务含义", "敏感级别"],
    },
    "enums": {
        "sheet": "03_枚举值字典",
        "required": ["字段标识", "字段名称", "枚举值", "枚举名称", "业务说明", "是否有效"],
    },
    "lineage": {
        "sheet": "04_数据血缘",
        "required": ["目标字段", "中文名称", "上游来源", "加工/计算规则", "下游流向", "血缘类型", "说明"],
    },
}

METRIC_SHEET = "05_指标字典"
METRIC_HEADERS = [
    "指标编码",
    "指标名称",
    "业务别名",
    "业务定义",
    "SQL模板",
    "允许维度",
    "时间字段",
]

SUPPORTED_DIMENSIONS = {"month", "category", "seller"}
ALLOWED_TABLE_TYPES = {"业务表", "配置表", "日志表", "中间表"}
ALLOWED_SENSITIVITY = {"低", "中", "高"}
ALLOWED_VALID_FLAGS = {"是", "否"}


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def split_list(value: Any) -> list[str]:
    text = as_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,，;；、]", text) if item.strip()]


def read_sheet(workbook, sheet_name: str, required_headers: list[str]) -> list[dict[str, str]]:
    if sheet_name not in workbook.sheetnames:
        raise ValueError(f"缺少 Sheet: {sheet_name}")

    ws = workbook[sheet_name]
    header_row = None
    headers: list[str] = []

    for row_no, row in enumerate(
        ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20), values_only=True),
        start=1,
    ):
        current_headers = [as_text(value) for value in row]
        if all(header in current_headers for header in required_headers):
            header_row = row_no
            headers = current_headers
            break

    if header_row is None:
        raise ValueError(f"{sheet_name} 未找到表头: {required_headers}")

    rows: list[dict[str, str]] = []
    for row in ws.iter_rows(
        min_row=header_row + 1,
        max_col=len(headers),
        values_only=True,
    ):
        values = [as_text(value) for value in row]
        if not any(values):
            continue
        rows.append(
            {
                headers[index]: values[index]
                for index in range(len(headers))
                if headers[index]
            }
        )
    return rows


def validate_catalog(
    tables: list[dict[str, str]],
    fields: list[dict[str, str]],
    enums: list[dict[str, str]],
    metrics: list[dict[str, str]],
) -> None:
    errors: list[str] = []

    table_names = [row["表名"] for row in tables]
    if len(table_names) != len(set(table_names)):
        errors.append("存在重复表名")

    for row in tables:
        table_name = row["表名"]
        if not table_name:
            errors.append("存在空表名")
        elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            errors.append(f"表名不合法: {table_name}")
        if row["表类型"] not in ALLOWED_TABLE_TYPES:
            errors.append(f"{table_name} 的表类型不合法: {row['表类型']}")

    field_ids = [row["字段名"] for row in fields]
    if len(field_ids) != len(set(field_ids)):
        errors.append("存在重复字段名")

    table_name_set = set(table_names)
    for row in fields:
        field_id = row["字段名"]
        if "." not in field_id:
            errors.append(f"字段名必须使用 表名.字段名 格式: {field_id}")
            continue
        table_name = field_id.split(".", 1)[0]
        if table_name not in table_name_set:
            errors.append(f"字段引用了不存在的表: {field_id}")
        if row["敏感级别"] not in ALLOWED_SENSITIVITY:
            errors.append(f"{field_id} 敏感级别不合法: {row['敏感级别']}")

    field_id_set = set(field_ids)
    for row in enums:
        if row["字段标识"] not in field_id_set:
            errors.append(f"枚举引用了不存在的字段: {row['字段标识']}")
        if row["是否有效"] not in ALLOWED_VALID_FLAGS:
            errors.append(f"枚举是否有效值不合法: {row['是否有效']}")

    metric_ids = [row["指标编码"] for row in metrics]
    if len(metric_ids) != len(set(metric_ids)):
        errors.append("存在重复指标编码")

    for row in metrics:
        metric_id = row["指标编码"]
        sql_template = row["SQL模板"].strip()

        if not sql_template:
            errors.append(f"指标 {metric_id} 缺少 SQL 模板")
            continue

        if ";" in sql_template:
            errors.append(f"指标 {metric_id} 的 SQL 模板不允许包含分号")

        if not re.match(r"^\s*(SELECT|WITH)\b", sql_template, re.IGNORECASE):
            errors.append(f"指标 {metric_id} 只允许 SELECT/WITH 查询")

        if sql_template.count("?") != 3:
            errors.append(
                f"指标 {metric_id} 的 SQL 必须包含 3 个 ? 占位符"
                "（start_date、end_date、limit）"
            )

        for dimension in split_list(row["允许维度"]):
            if dimension not in SUPPORTED_DIMENSIONS:
                errors.append(
                    f"指标 {metric_id} 使用了未支持的维度: {dimension}"
                )

    if errors:
        raise ValueError("业务字典校验失败:\n" + "\n".join(f"- {e}" for e in errors))


def build_catalog() -> dict[str, Any]:
    workbook = load_workbook(EXCEL_PATH, read_only=True, data_only=True)

    try:
        table_rows = read_sheet(
            workbook,
            SHEET_SPECS["tables"]["sheet"],
            SHEET_SPECS["tables"]["required"],
        )
        field_rows = read_sheet(
            workbook,
            SHEET_SPECS["fields"]["sheet"],
            SHEET_SPECS["fields"]["required"],
        )
        enum_rows = read_sheet(
            workbook,
            SHEET_SPECS["enums"]["sheet"],
            SHEET_SPECS["enums"]["required"],
        )
        lineage_rows = read_sheet(
            workbook,
            SHEET_SPECS["lineage"]["sheet"],
            SHEET_SPECS["lineage"]["required"],
        )

        if METRIC_SHEET in workbook.sheetnames:
            metric_rows = read_sheet(workbook, METRIC_SHEET, METRIC_HEADERS)
        else:
            metric_rows = []

    finally:
        workbook.close()

    validate_catalog(table_rows, field_rows, enum_rows, metric_rows)

    enums_by_field: dict[str, list[dict[str, str]]] = {}
    for row in enum_rows:
        field_id = row["字段标识"]
        enums_by_field.setdefault(field_id, []).append(
            {
                "枚举值": row["枚举值"],
                "枚举名称": row["枚举名称"],
                "业务说明": row["业务说明"],
                "是否有效": row["是否有效"],
            }
        )

    metrics: dict[str, dict[str, Any]] = {}
    for row in metric_rows:
        metrics[row["指标编码"]] = {
            "指标编码": row["指标编码"],
            "指标名称": row["指标名称"],
            "业务别名": split_list(row["业务别名"]),
            "业务定义": row["业务定义"],
            "SQL模板": row["SQL模板"].strip(),
            "允许维度": split_list(row["允许维度"]),
            "时间字段": row["时间字段"],
        }

    catalog = {
        "meta": {
            "schema_version": "1.0",
            "source_file": str(EXCEL_PATH),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "tables": {row["表名"]: row for row in table_rows},
        "fields": {row["字段名"]: row for row in field_rows},
        "enums": enums_by_field,
        "lineage": lineage_rows,
        "metrics": metrics,
    }

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"已生成: {CATALOG_PATH}")
    print(
        f"表: {len(table_rows)}, "
        f"字段: {len(field_rows)}, "
        f"枚举字段: {len(enums_by_field)}, "
        f"血缘: {len(lineage_rows)}, "
        f"指标: {len(metrics)}"
    )
    return catalog


if __name__ == "__main__":
    build_catalog()