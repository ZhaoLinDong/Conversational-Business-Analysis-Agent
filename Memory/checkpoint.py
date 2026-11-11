"""
检查点器工厂
- InMemorySaver：存内存，重启丢失（开发测试用）
- SqliteSaver：存 SQLite 文件，重启恢复（需 pip install langgraph-checkpoint-sqlite）
"""
import os
import sqlite3

from langgraph.checkpoint.memory import InMemorySaver

# 持久化文件路径：统一放在 Memory 目录下（绝对路径，避免跑偏）
_CHECKPOINT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.db")

# SqliteSaver 延迟导入：
# langgraph-checkpoint-sqlite 是独立扩展包，未安装时不影响内存版正常使用
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    _SQLITE_SAVER_AVAILABLE = True
except ImportError:
    SqliteSaver = None
    _SQLITE_SAVER_AVAILABLE = False


def create_checkpointer(persistent: bool = False):
    """
    创建检查点器

    Args:
        persistent: True=SQLite 持久化（重启不丢），False=内存版（重启丢失）

    Returns:
        checkpointer 实例
    """
    if not persistent:
        return InMemorySaver()

    if not _SQLITE_SAVER_AVAILABLE:
        raise ImportError(
            "SQLite 持久化需要安装依赖: pip install langgraph-checkpoint-sqlite"
        )

    conn = sqlite3.connect(_CHECKPOINT_DB, check_same_thread=False)
    return SqliteSaver(conn)