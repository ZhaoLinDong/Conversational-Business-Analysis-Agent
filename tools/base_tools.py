"""
基础工具定义
"""
import math
import datetime

from typing import Optional

from langchain_core.tools import tool

from simpleeval import simple_eval

from tools.business_metric_tools import search_business_metrics, query_business_metric
from tools.excel import excel_to_sqlite, batch_import_from_dir
from tools.query_data_tool import query_data
from tools.schema_tools import search_business_schema
from tools.sql_tools import run_readonly_sql

# 数学函数白名单 - 与 calculate 的 docstring 保持一致
_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "log": math.log,
    "floor": math.floor,
}



@tool
def get_current_time() -> str:
    """获取当前时间。当你需要知道现在几点、今天几号时使用这个工具。"""
    now = datetime.datetime.now()
    return now.strftime("%Y年%m月%d日 %H:%M:%S")


@tool
def calculate(expression: str) -> str:
    """计算数学表达式。支持 + - * / // % ** 与括号，以及 sqrt/abs/round/log/floor，例如 (1+2)*3.5、2**10、sqrt(16)。
    支持函数: sqrt(x) 平方根, abs(x) 绝对值, round(x) 四舍五入, log(x) 自然对数(以e为底), floor(x) 向下取整。
    Args:
        expression: 纯数学表达式，不要传自然语言，不要带等号。
    """
    expression = expression.strip()
    if len(expression) > 200:
        raise ValueError("表达式过长，上限 200 字符")
    value = simple_eval(expression, functions=_FUNCS)
    if type(value) not in (int, float):  # 严格检查，挡掉字符串、列表、bool 等非数值结果
        raise ValueError(f"计算结果不是数值（得到 {type(value).__name__}）")
    return f"{expression} = {value}"


@tool
def search_knowledge_base(query: str) -> str:
    """
    搜索知识库。当你需要查找特定信息时使用这个工具。

    Args:
        query: 搜索关键词
    """
    # 模拟知识库搜索结果
    knowledge = {
        "python": "Python 是一种广泛使用的高级编程语言，由 Guido van Rossum 于 1991 年创建。",
        "langchain": "LangChain 是一个用于开发大语言模型应用的框架，提供了工具调用、记忆、链等功能。",
        "智能体": "智能体（Agent）是能够自主决策、调用工具完成任务的 AI 系统。",
    }

    query_lower = query.lower()
    for key, value in knowledge.items():
        if key in query_lower:
            return value

    return f"未找到关于 '{query}' 的信息。"

@tool
def import_data_to_db(file_path: str, table_name: Optional[str] = None) -> str:
    """读取数据文件并导入 SQLite 统一数据库（ecommerce.db）。
    - 表格文件(.csv/.xlsx/.xls)：以文件名建表；Excel 多 sheet 时每个 sheet 建一张表
      （表名=文件名_sheet名）
    - JSON 文件：解析成表
    - 文本文件(.txt/.md，如说明文档、练习题)：统一存入 documents 文档表
    当用户需要把文件内容存入数据库时使用这个工具。

    Args:
        file_path: 数据文件的完整路径，例如 "D:/data/月度销售.xlsx"
        table_name: 目标表名，可选，仅在单 sheet/单表导入时生效
    """
    return excel_to_sqlite(file_path, table_name, if_exists="append")

@tool
def batch_import_from_folder(folder_path: str) -> str:
    """批量导入文件夹下的所有数据文件（.xlsx/.xls/.csv）到 SQLite 数据库。
    每个文件会创建一个以文件名命名的独立数据库。单个文件失败不影响其他文件。
    当用户需要一次导入多个表格文件时使用这个工具。

    Args:
        folder_path: 包含数据文件的文件夹完整路径，例如 "D:/data/电商数据"
    """
    return batch_import_from_dir(folder_path)


@tool
def read_local_file(file_path: str) -> str:
    """读取本地文本文件内容，仅支持 txt / md / json。
    Args:
        file_path: 文件相对路径
    """
    # 加安全校验：限制根目录，防止 ../ 越权

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read(4000) # 截断
    return f"读取成功"

@tool
def write_local_file(file_path: str, content: str) -> str:
    """写入文本到本地文件，会覆盖原有内容。
    Args:
        file_path: 文件路径
        content: 写入文本
    """
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"成功写入 {file_path}"




# 工具列表 - 导出给智能体使用
ALL_TOOLS = [
    get_current_time,
    calculate,
    search_knowledge_base,
    import_data_to_db,
    batch_import_from_folder,
    search_business_metrics,
    query_business_metric,
    query_data,
    search_business_schema,
    run_readonly_sql,
]


