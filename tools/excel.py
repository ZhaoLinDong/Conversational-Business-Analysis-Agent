"""
数据文件导入 SQLite 业务模块
格式支持：真 Excel（多 sheet）/ 伪 Excel（CSV、HTML 伪装）/ CSV / JSON / 文本
流程：读取 → 清洗 → 单库多表写入
"""
import json as json_lib
import os
import re
import sqlite3

import pandas as pd

from config import SQLITE_DB_DIR, SQLITE_DB_PATH

# 支持的扩展名分类
TABLE_EXTS = (".xlsx", ".xls")           # Excel 类（可能是真 Excel 或伪装格式）
DOC_EXTS = (".txt", ".md")               # 文本类 → 导入为文档
SUPPORTED_EXTS = TABLE_EXTS + DOC_EXTS + (".csv", ".json")

# 文档统一存放的表名
DOCUMENTS_TABLE = "documents"

# pandas dtype → SQLite 类型映射
TYPE_MAPPING = {
    "int64": "INTEGER",
    "int32": "INTEGER",
    "float64": "REAL",
    "float32": "REAL",
    "bool": "INTEGER",
}


def get_sqlite_type(dtype) -> str:
    """将 pandas dtype 转换为 SQLite 类型"""
    dtype_str = str(dtype)
    if dtype_str.startswith("datetime64"):
        return "TEXT"  # SQLite 没有原生日期类型，存字符串
    return TYPE_MAPPING.get(dtype_str, "TEXT")


def sanitize_name(name: str) -> str:
    """清洗表名/列名：只保留字母、数字、下划线和中文，防止 SQL 语法错误"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "_", str(name)).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = "col_" + cleaned
    return cleaned or "unnamed"


def read_text_file(file_path: str) -> str:
    """读取文本文件内容（utf-8 失败自动降级 gbk）"""
    for encoding in ("utf-8", "gbk"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别文件编码: {file_path}")


def _looks_like_html(file_path: str) -> bool:
    """嗅探文件开头是否为 HTML（网页/系统导出的伪 Excel 常见形态）"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(1000).lower()
        return "<table" in head or "<html" in head
    except OSError:
        return False


def read_excel_sheets(file_path: str, sheet_name=None) -> dict:
    """
    读取 Excel 的一个或全部工作表（带伪装格式降级）

    格式降级链：
    1. 真 Excel（xlsx/xls）→ pd.read_excel
    2. 读取失败 + HTML 特征 → pd.read_html（网页表格）
    3. 读取失败 + 无 HTML 特征 → 按 CSV 读（utf-8 → gbk）
    4. 全部失败 → 抛出友好错误

    Returns:
        {sheet名: DataFrame}，空 sheet 已过滤
    """
    try:
        # sheet_name=None 时返回 {sheet名: DataFrame}
        sheets = pd.read_excel(file_path, sheet_name=sheet_name)
    except ValueError:
        # ValueError: Excel file format cannot be determined → 不是真 Excel
        if _looks_like_html(file_path):
            tables = pd.read_html(file_path)
            if not tables:
                raise ValueError(f"文件中未找到表格数据: {file_path}")
            return {"data": tables[0]}

        for kwargs in ({}, {"encoding": "gbk"}):
            try:
                return {"data": pd.read_csv(file_path, **kwargs)}
            except UnicodeDecodeError:
                continue

        raise ValueError(
            f"无法识别文件格式: {file_path}\n"
            "该文件不是有效的 Excel（.xlsx/.xls），也不是 CSV 或 HTML 表格，"
            "请确认导出文件是否损坏。"
        )

    # 正常 Excel 路径：统一包装成 {sheet名: DataFrame}
    if isinstance(sheets, pd.DataFrame):
        return {str(sheet_name): sheets}
    return {name: df for name, df in dict(sheets).items() if not df.empty}


def read_data_file(file_path: str):
    """
    读取非 Excel 类数据文件（csv/json/txt/md）

    Returns:
        (df, kind): df 为 DataFrame，kind 为 "table" 或 "doc"
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        try:
            return pd.read_csv(file_path), "table"
        except UnicodeDecodeError:
            return pd.read_csv(file_path, encoding="gbk"), "table"

    if ext == ".json":
        try:
            raw = json_lib.loads(read_text_file(file_path))
            return pd.json_normalize(raw), "table"
        except (json_lib.JSONDecodeError, ValueError):
            return pd.DataFrame({"content": [read_text_file(file_path)]}), "doc"

    if ext in DOC_EXTS:
        text = read_text_file(file_path)
        return (
            pd.DataFrame({
                "file_name": [os.path.basename(file_path)],
                "content": [text],
            }),
            "doc",
        )

    raise ValueError(f"不支持的文件格式: {ext}，支持 .xlsx/.xls/.csv/.json/.txt/.md")


def _prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """清洗列名 + 日期列转字符串（Python 3.12+ 的 sqlite3 不再自动适配 datetime）"""
    df = df.copy()
    df.columns = [sanitize_name(c) for c in df.columns]
    for col in df.columns:
        if str(df[col].dtype).startswith("datetime64"):
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def _insert_dataframe(conn, df: pd.DataFrame, table_name: str, if_exists: str) -> int:
    """在已打开的连接中建表并批量插入，返回插入行数"""
    cur = conn.cursor()

    if if_exists == "replace":
        cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')

    columns_sql = ", ".join(f'"{c}" {get_sqlite_type(df[c].dtype)}' for c in df.columns)
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({columns_sql})')

    data = [
        tuple(None if pd.isna(v) else v for v in row)
        for row in df.itertuples(index=False)
    ]
    col_names = ", ".join(f'"{c}"' for c in df.columns)
    placeholders = ", ".join("?" for _ in df.columns)
    cur.executemany(
        f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})',
        data,
    )
    return len(data)


def excel_to_sqlite(
    file_path: str,
    table_name: str = None,
    sheet_name=None,
    if_exists: str = "append",
) -> str:
    """
    主函数：读取数据文件 → 在统一数据库（ecommerce.db）中建表 → 插入数据

    多 sheet 处理规则：
    - Excel 默认读取全部 sheet，每个 sheet 一张独立表
    - 单 sheet 表名 = 文件名；多 sheet 表名 = 文件名_sheet名
    - 指定 sheet_name 时只导入该 sheet

    Args:
        file_path: 数据文件路径（支持 .xlsx/.xls/.csv/.json/.txt/.md）
        table_name: 目标表名（仅单表导入时生效）
        sheet_name: Excel sheet 名或索引，None=全部 sheet
        if_exists: 表已存在时的处理方式，"append" 追加 / "replace" 重建

    Returns:
        导入结果报告
    """
    ext = os.path.splitext(file_path)[1].lower()
    file_stem = sanitize_name(os.path.splitext(os.path.basename(file_path))[0])

    # 1. 收集待导入数据: (目标表名, df, 原始sheet名)
    if ext in TABLE_EXTS:
        sheets = read_excel_sheets(file_path, sheet_name)
        if not sheets:
            return "Excel 中没有可导入的数据（所有 sheet 均为空）"

        tasks = []
        for sheet_key, df in sheets.items():
            if len(sheets) > 1:
                # 多 sheet：直接用 sheet 名做表名（orders/customers 等），
                # 与业务数据说明表、查询元数据的标准表名对齐
                target_name = sanitize_name(sheet_key)
            else:
                # 单 sheet：文件名（或自定义表名）
                target_name = sanitize_name(table_name) if table_name else file_stem
            tasks.append((target_name, df, sheet_key))
    else:
        df, kind = read_data_file(file_path)
        if kind == "doc":
            target_name = DOCUMENTS_TABLE
        else:
            target_name = sanitize_name(table_name) if table_name else file_stem
        tasks = [(target_name, df, None)]

    # 2. 单次连接完成全部写入
    os.makedirs(SQLITE_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    try:
        lines = []
        for target_name, df, sheet_key in tasks:
            df = _prepare_dataframe(df)
            row_count = _insert_dataframe(conn, df, target_name, if_exists)
            suffix = f"（sheet: {sheet_key}）" if sheet_key is not None else ""
            lines.append(f"- {target_name}: {row_count} 行 × {len(df.columns)} 列{suffix}")
        conn.commit()

        is_excel = ext in TABLE_EXTS
        unit = "工作表" if is_excel else "数据表"
        return (
            f"成功导入 {len(tasks)} 个{unit}\n"
            f"数据库: {SQLITE_DB_PATH}\n" + "\n".join(lines)
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def batch_import_from_dir(folder_path: str, if_exists: str = "append") -> str:
    """
    批量导入：遍历文件夹下所有数据文件，逐个导入统一数据库（单库多表）
    """
    if not os.path.isdir(folder_path):
        raise ValueError(f"目录不存在: {folder_path}")

    files = [
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    ]

    if not files:
        return f"目录 '{folder_path}' 中没有找到支持的数据文件"

    success, failed = [], []
    for fp in files:
        try:
            excel_to_sqlite(fp, if_exists=if_exists)
            success.append(os.path.basename(fp))
        except Exception as exc:
            failed.append((os.path.basename(fp), str(exc)))

    lines = [
        f"批量导入完成: 共 {len(files)} 个文件，成功 {len(success)} 个，失败 {len(failed)} 个",
        f"目标数据库: {SQLITE_DB_PATH}",
    ]
    if success:
        lines.append("✅ 成功:")
        for name in success:
            lines.append(f"  - {name}")
    if failed:
        lines.append("❌ 失败:")
        for name, err in failed:
            lines.append(f"  - {name}: {err}")
    return "\n".join(lines)