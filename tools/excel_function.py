import sqlite3
from enum import Enum
from typing import List, Dict, Any, Optional


# ========== 1. 静态枚举类 ==========
class QueryType(Enum):
    """查询类型枚举"""
    SUMMARY = "汇总查询"
    CONDITION_SUMMARY = "条件汇总查询"
    CHAIN = "环比"
    RANK = "排名"
    JOIN = "关联"


# ========== 2. SQLite 数据库操作基础类 ==========
class SqliteHelper:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.cur: Optional[sqlite3.Cursor] = None

    def connect(self):
        """连接数据库"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 支持按列名取值
        self.cur = self.conn.cursor()

    def close(self):
        """关闭连接"""
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def execute_sql(self, sql: str, params: tuple = ()) -> int:
        """执行增删改，返回影响行数"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        self.cur.execute(sql, params)
        self.conn.commit()
        return self.cur.rowcount

    def query_sql(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """查询，返回字典列表"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        self.cur.execute(sql, params)
        rows = self.cur.fetchall()
        result = [dict(row) for row in rows]
        return result


# ========== 3. 各个查询函数（一个一个独立函数） ==========
def summary_query(db: SqliteHelper, metric: str, table: str) -> List[Dict[str, Any]]:
    """
    汇总查询
    :param db: sqlite助手实例
    :param metric: 指标列名
    :param table: 表名
    :return: 结果集
    """
    sql = f"SELECT {metric} FROM {table}"
    return db.query_sql(sql)


def condition_summary_query(db: SqliteHelper, filter_params: dict) -> List[Dict[str, Any]]:
    """
    条件汇总查询
    :param db: sqlite助手实例
    :param filter_params: 过滤参数字典
    :return: 结果集
    """
    # 示例：根据条件汇总，这里仅框架，你自行拼接安全参数
    sql = """
    -- TODO 替换带where条件的汇总SQL
    SELECT COUNT(*) as total FROM table_name WHERE status = ?
    """
    params = (filter_params.get("status"),)
    res = db.query_sql(sql, params)
    return res


def chain_query(db: SqliteHelper, compare_date: str) -> List[Dict[str, Any]]:
    """
    环比查询
    :param db: sqlite助手实例
    :param compare_date: 对比日期/月份
    :return: 结果集
    """
    sql = """
    -- TODO 环比SQL，当前周期 & 上一周期对比计算环比增长率
    SELECT curr.val, prev.val, (curr.val - prev.val)*1.0 / prev.val as chain_rate
    FROM curr_data curr
    LEFT JOIN prev_data prev ON 1=1
    """
    res = db.query_sql(sql, (compare_date,))
    return res


def rank_query(db: SqliteHelper, top_n: int = 10) -> List[Dict[str, Any]]:
    """
    排名查询
    :param db: sqlite助手实例
    :param top_n: 取前N名
    :return: 结果集
    """
    sql = """
    -- TODO 排名SQL，sqlite用row_number窗口函数（sqlite3.25+支持）
    SELECT *, ROW_NUMBER() OVER(ORDER BY score DESC) AS rk
    FROM table_name
    LIMIT ?
    """
    res = db.query_sql(sql, (top_n,))
    return res


def join_query(db: SqliteHelper) -> List[Dict[str, Any]]:
    """
    关联查询（多表join）
    :param db: sqlite助手实例
    :return: 结果集
    """
    sql = """
    -- TODO 多表关联SQL
    SELECT a.*, b.name
    FROM table_a a
    LEFT JOIN table_b b ON a.b_id = b.id
    """
    res = db.query_sql(sql)
    return res

