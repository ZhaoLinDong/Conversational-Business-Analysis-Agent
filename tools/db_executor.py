"""数据库只读执行工具。

该模块不是 LangChain Tool，只负责安全执行已校验、已参数化的 SQL。
"""
import sqlite3
from typing import Any
import time
from config import SQLITE_DB_PATH


def execute_readonly_query(
    sql: str,
    params: list[Any],
    db_path: str | None = None,
    query_timeout_seconds: float = 10.0,
) -> tuple[list[str], list[dict]]:
    """执行只读 SQL 并返回列名和数据行。

    Args:
        sql: 只允许 SELECT 语句。
        params: SQL 参数数组。
        db_path: 数据库路径；不传时使用全局 SQLITE_DB_PATH。

    Returns:
        tuple:
            - 列名列表；
            - 行数据字典列表。

    Raises:
        ValueError: SQL 不是 SELECT。
        sqlite3.Error: 数据库连接或执行失败。
    """
    normalized_sql = sql.lstrip()
    if query_timeout_seconds <= 0:
        raise ValueError("query_timeout_seconds 必须大于 0")
    sql_head = normalized_sql.upper()

    if not (
            sql_head.startswith("SELECT")
            or sql_head.startswith("WITH")
    ):
        raise ValueError(
            "只允许执行 SELECT 或 WITH ... SELECT 查询"
        )

    database = db_path or SQLITE_DB_PATH
    db_posix = database.replace("\\", "/")

    conn = sqlite3.connect(
        f"file:{db_posix}?mode=ro",
        uri=True,
        timeout=5,
    )

    timed_out = False
    deadline = time.monotonic() + query_timeout_seconds

    def progress_handler() -> int:
        nonlocal timed_out
        if time.monotonic() >= deadline:
            timed_out = True
            return 1
        return 0

    try:
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(progress_handler, 1000)

        cursor = conn.execute(sql, params)
        rows_raw = cursor.fetchall()

        columns = (
            [description[0] for description in cursor.description]
            if cursor.description is not None
            else []
        )
        rows = [dict(row) for row in rows_raw]

        return columns, rows

    except sqlite3.OperationalError as exc:
        if timed_out:
            raise TimeoutError(
                f"SQL 执行超过 {query_timeout_seconds:.1f} 秒"
            ) from exc
        raise

    finally:
        conn.close()

