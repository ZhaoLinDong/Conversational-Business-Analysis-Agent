"""
对话记忆管理
- 维护多轮对话历史（模型不再"失忆"）
- 持久化到 JSON 文件，程序重启不丢失
- 自动裁剪历史长度，避免 token 爆炸
"""
import json
import os

from langchain_core.messages import HumanMessage, AIMessage

# 默认持久化路径：本文件所在目录下的 history.json（绝对路径，避免跑偏）
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")


class ConversationMemory:
    """对话记忆管理器"""

    def __init__(self, max_turns: int = 20, persist_path: str = None):
        """
        Args:
            max_turns: 最多保留多少轮对话（一轮 = 一问一答），超出自动丢弃最早的
            persist_path: 持久化文件路径，默认 Memory/history.json
        """
        self.max_turns = max_turns
        self.persist_path = persist_path or _DEFAULT_PATH
        self.messages = []  # LangChain Message 对象列表
        self.load()

    def add(self, user_input: str, reply: str):
        """记录一轮对话（用户输入 + AI 回复）"""
        self.messages.append(HumanMessage(content=user_input))
        self.messages.append(AIMessage(content=reply))

        # 按轮数裁剪（每轮 2 条消息）
        max_messages = self.max_turns * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

        self.save()

    def get_messages(self):
        """返回历史消息列表（供 prompt 的 chat_history 使用）"""
        return list(self.messages)

    def clear(self):
        """清空记忆"""
        self.messages = []
        self.save()

    def save(self):
        """持久化到 JSON 文件"""
        data = [
            {
                "role": "user" if isinstance(m, HumanMessage) else "assistant",
                "content": m.content,
            }
            for m in self.messages
        ]
        try:
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"记忆保存失败: {e}")

    def load(self):
        """程序启动时从 JSON 文件恢复记忆"""
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.messages = [
                HumanMessage(content=item["content"]) if item["role"] == "user"
                else AIMessage(content=item["content"])
                for item in data
            ]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"记忆加载失败，使用空记忆: {e}")
            self.messages = []