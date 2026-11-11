"""
使用 CustomTkinter 创建现代化桌面聊天界面
安装：pip install customtkinter
"""
import traceback
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import customtkinter as ctk
from tkinter import END, filedialog, messagebox
import threading
from Model_core.Agent_with_tools import ToolAgent  # ← 改成带工具的智能体
import math
import matplotlib

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False

# 设置主题
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ChatApp:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("智能体对话")
        self.root.geometry("1200x700")  # ← 加宽窗口

        # 创建带工具的智能体
        self.agent = ToolAgent(model_name="qwen-plus")

        self._create_widgets()

    def _create_widgets(self):
        """创建界面"""
        # 标题
        title = ctk.CTkLabel(
            self.root,
            text="🤖 智能体对话",
            font=ctk.CTkFont(size=24, weight="bold")
        )
        title.pack(pady=10)

        # 主容器（左右布局）
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 左侧：工具列表
        tools_frame = ctk.CTkFrame(main_frame, width=300)
        tools_frame.pack(side="left", fill="y", padx=(0, 10))
        tools_frame.pack_propagate(False)

        tools_title = ctk.CTkLabel(
            tools_frame,
            text="🔧 可用工具",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        tools_title.pack(pady=10)

        # 工具列表显示区域
        self.tools_display = ctk.CTkTextbox(
            tools_frame,
            font=ctk.CTkFont(size=12),
            wrap="word",
            state="disabled"
        )
        self.tools_display.pack(fill="both", expand=True, padx=10, pady=10)

        # 显示工具列表
        self._show_tools()

        # 右侧：聊天区域
        chat_frame = ctk.CTkFrame(main_frame)
        chat_frame.pack(side="right", fill="both", expand=True)

        # 聊天显示区域
        self.chat_display = ctk.CTkTextbox(
            chat_frame,
            font=ctk.CTkFont(size=14),
            wrap="word",
            state="disabled"
        )
        # 图表面板：默认隐藏，有图表结果时显示
        self.chart_frame = ctk.CTkFrame(
            chat_frame,
            height=300,
        )
        self.chart_frame.pack_propagate(False)

        self.chart_title = ctk.CTkLabel(
            self.chart_frame,
            text="📊 分析图表",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.chart_title.pack(anchor="w", padx=12, pady=(8, 0))

        self.chart_area = ctk.CTkFrame(
            self.chart_frame,
            fg_color="transparent",
        )
        self.chart_area.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 8),
        )
        self.chat_display.pack(fill="both", expand=True, padx=10, pady=10)

        # 输入区域
        input_frame = ctk.CTkFrame(chat_frame)
        input_frame.pack(fill="x", padx=10, pady=10)
        self.input_frame = input_frame

        self.input_box = ctk.CTkTextbox(
            input_frame,
            height=80,
            font=ctk.CTkFont(size=14)
        )
        self.input_box.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.input_box.bind("<Return>", self._on_enter)


        # 右侧按钮组：📎 选择文件 / 📁 选择文件夹 / 发送
        btn_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        btn_frame.pack(side="right", fill="y")

        file_btn = ctk.CTkButton(
            btn_frame,
            text="📎 选择文件",
            width=100,
            command=self._pick_file
        )
        file_btn.pack(side="top", pady=(0, 5))

        folder_btn = ctk.CTkButton(
            btn_frame,
            text="📁 选择文件夹",
            width=100,
            command=self._pick_folder
        )
        folder_btn.pack(side="top", pady=(0, 5))

        send_btn = ctk.CTkButton(
            btn_frame,
            text="发送",
            width=100,
            command=self._send_message
        )
        send_btn.pack(side="bottom")



        # 欢迎消息
        self._append_message("AI", "你好！我是智能体，可以使用左侧的工具来帮助你。")

    def _show_tools(self):
        """显示工具列表"""
        self.tools_display.configure(state="normal")
        self.tools_display.delete("1.0", END)

        for i, tool in enumerate(self.agent.tools, 1):
            # 工具名称（用粗体标记）
            self.tools_display.insert(END, f"{i}. {tool.name}\n")
            # 工具描述
            self.tools_display.insert(END, f"   {tool.description}\n\n")

        self.tools_display.configure(state="disabled")

        # 配置文本样式
        self.tools_display._textbox.tag_configure(
            "title", foreground="#2196F3", font=("Segoe UI", -13, "bold"))
        self.tools_display._textbox.tag_configure(
            "desc", foreground="#AAAAAA", font=("Segoe UI", -12))

    def _append_message(self, sender, message):
        """添加消息"""
        self.chat_display.configure(state="normal")
        self.chat_display.insert(END, f"{sender}: {message}\n\n")
        self.chat_display.see(END)
        self.chat_display.configure(state="disabled")

    def _clear_chart(self):
        """清理旧图表并隐藏图表面板。"""
        chart_area = getattr(self, "chart_area", None)
        chart_frame = getattr(self, "chart_frame", None)

        if chart_area is not None:
            for widget in chart_area.winfo_children():
                widget.destroy()

        figure = getattr(self, "_chart_figure", None)
        if figure is not None:
            figure.clear()
            self._chart_figure = None

        self._chart_canvas = None

        if chart_frame is not None:
            chart_frame.pack_forget()

    @staticmethod
    def _infer_chart_spec(
            rows: list[dict],
            metric_name: str | None = None,
            time_range: str | None = None,
    ) -> dict | None:
        """根据数据行自动推断图表规格。

        支持三种数据形态：
        1. 多行长表：分类字段 + 数值字段；
        2. 单行宽表：同一行中的多个数值字段；
        3. 单行单指标：分类字段 + 一个数值字段。
        """
        if not isinstance(rows, list) or not rows:
            return None

        keys: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in row:
                if isinstance(key, str) and key not in keys:
                    keys.append(key)

        if not keys:
            return None

        def to_number(value):
            if value is None or isinstance(value, bool):
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number):
                return None
            return number

        numeric_fields: list[str] = []
        categorical_fields: list[str] = []

        for key in keys:
            values = [
                row.get(key)
                for row in rows
                if isinstance(row, dict)
            ]
            non_null_values = [
                value
                for value in values
                if value is not None and str(value).strip() != ""
            ]

            if not non_null_values:
                continue

            numeric_values = [
                to_number(value)
                for value in non_null_values
            ]

            if all(value is not None for value in numeric_values):
                normalized_key = key.casefold()
                if "编号" not in key and "id" not in normalized_key:
                    numeric_fields.append(key)
                continue

            string_values = [
                str(value).strip()
                for value in non_null_values
            ]

            if len(rows) >= 2:
                if len(set(string_values)) >= 2:
                    categorical_fields.append(key)
            else:
                categorical_fields.append(key)

        if not numeric_fields:
            return None

        category_hints = (
            "名称",
            "品类",
            "类别",
            "商家",
            "客户",
            "城市",
            "地区",
            "日期",
            "月份",
            "季度",
            "年份",
            "状态",
            "渠道",
            "方式",
        )

        def category_score(field_name: str) -> float:
            score = 0.0
            if any(hint in field_name for hint in category_hints):
                score += 3.0

            field_values = [
                str(row.get(field_name, "")).strip()
                for row in rows
                if isinstance(row, dict)
            ]
            average_length = (
                sum(len(value) for value in field_values) / len(field_values)
                if field_values
                else 0
            )
            score += min(average_length, 20.0) / 20.0

            normalized_key = field_name.casefold()
            if "编号" in field_name or "id" in normalized_key:
                score -= 2.0

            return score

        value_hints = (
            "销售额",
            "金额",
            "数量",
            "订单数",
            "客户数",
            "评分",
            "延迟率",
            "占比",
            "比例",
            "百分比",
            "收入",
            "销量",
            "price",
            "amount",
            "count",
            "rate",
            "score",
        )

        def value_score(field_name: str) -> float:
            score = 0.0
            if any(
                    hint in field_name.casefold()
                    for hint in value_hints
            ):
                score += 3.0

            if metric_name and field_name in metric_name:
                score += 5.0

            return score

        x_field = (
            max(categorical_fields, key=category_score)
            if categorical_fields
            else None
        )

        # 单行数据
        if len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]

            matched_numeric_fields = [
                field_name
                for field_name in numeric_fields
                if metric_name and field_name in metric_name
            ]

            # 单行分类结果：例如单商家延迟率
            if x_field and matched_numeric_fields:
                y_field = max(
                    matched_numeric_fields,
                    key=value_score,
                )
                y_value = to_number(row.get(y_field))
                x_value = str(row.get(x_field, "")).strip()

                if y_value is not None and x_value:
                    title = metric_name or y_field
                    if time_range:
                        title = f"{title}（{time_range}）"

                    return {
                        "chart_type": "bar",
                        "title": title,
                        "x_field": x_field,
                        "y_field": y_field,
                        "x_label": x_field,
                        "y_label": y_field,
                        "data": [
                            {
                                x_field: x_value,
                                y_field: y_value,
                            }
                        ],
                    }

            # 单行多指标：例如延迟评分 vs 准时评分
            if len(numeric_fields) >= 2:
                data = []
                for field_name in numeric_fields:
                    value = to_number(row.get(field_name))
                    if value is None:
                        continue
                    data.append(
                        {
                            "对比项": field_name,
                            "数值": value,
                        }
                    )

                if len(data) >= 2:
                    title = metric_name or "指标对比"
                    if time_range:
                        title = f"{title}（{time_range}）"

                    return {
                        "chart_type": "bar",
                        "title": title,
                        "x_field": "对比项",
                        "y_field": "数值",
                        "x_label": "指标",
                        "y_label": "数值",
                        "data": data,
                    }

            # 单行单指标 + 分类字段
            if x_field and numeric_fields:
                y_field = max(numeric_fields, key=value_score)
                y_value = to_number(row.get(y_field))
                x_value = str(row.get(x_field, "")).strip()

                if y_value is not None and x_value:
                    title = metric_name or y_field
                    if time_range:
                        title = f"{title}（{time_range}）"

                    return {
                        "chart_type": "bar",
                        "title": title,
                        "x_field": x_field,
                        "y_field": y_field,
                        "x_label": x_field,
                        "y_label": y_field,
                        "data": [
                            {
                                x_field: x_value,
                                y_field: y_value,
                            }
                        ],
                    }

            return None

        # 多行长表
        if not x_field:
            return None

        y_field = max(numeric_fields, key=value_score)

        chart_type = "bar"

        if (
                len(rows) >= 3
                and any(
            hint in x_field
            for hint in ("月份", "日期", "时间", "季度", "年份")
        )
        ):
            chart_type = "line"
        elif any(
                hint in y_field
                for hint in ("占比", "比例", "百分比")
        ):
            chart_type = "pie"

        title = f"{metric_name or y_field}：按{x_field}"
        if time_range:
            title = f"{title}（{time_range}）"

        return {
            "chart_type": chart_type,
            "title": title,
            "x_field": x_field,
            "y_field": y_field,
            "x_label": x_field,
            "y_label": y_field,
        }


    def _render_chart(
        self,
        rows: list[dict],
        chart_spec: dict | None,
    ) -> None:
        """根据结构化图表规格渲染 matplotlib 图表。"""
        self._clear_chart()

        if not chart_spec:
            return

        if hasattr(chart_spec, "model_dump"):
            chart_spec = chart_spec.model_dump()

        if not isinstance(chart_spec, dict):
            return

        # 优先使用自动推断时生成的转换数据；
        # 没有 data 时继续使用原始 rows。
        chart_data = chart_spec.get("data")
        if isinstance(chart_data, list) and chart_data:
            source_rows = chart_data
        else:
            if not rows:
                return
            source_rows = rows

        chart_type = chart_spec.get("chart_type")
        if chart_type not in {"bar", "line", "pie"}:
            return

        x_field = chart_spec.get("x_field")
        y_field = chart_spec.get("y_field")
        if not isinstance(x_field, str) or not isinstance(y_field, str):
            return

        points: list[tuple[str, float]] = []

        for row in source_rows:
            if not isinstance(row, dict):
                continue
            if x_field not in row or y_field not in row:
                continue

            raw_value = row.get(y_field)
            if isinstance(raw_value, bool):
                continue

            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue

            if not math.isfinite(value):
                continue

            x_value = str(row.get(x_field, "")).strip()
            if not x_value:
                continue

            points.append((x_value, value))

        if not points:
            return

        # 图表最多展示前 20 个数据点，避免标签过度拥挤
        points = points[:20]

        if chart_type == "pie":
            if any(value < 0 for _, value in points):
                return
            if sum(value for _, value in points) <= 0:
                return

        x_values = [item[0] for item in points]
        y_values = [item[1] for item in points]

        title = str(
            chart_spec.get("title")
            or "数据分析图表"
        )
        x_label = str(
            chart_spec.get("x_label")
            or x_field
        )
        y_label = str(
            chart_spec.get("y_label")
            or y_field
        )

        try:
            figure = Figure(
                figsize=(8, 3.2),
                dpi=100,
                facecolor="#2b2b2b",
            )
            axes = figure.add_subplot(111)
            axes.set_facecolor("#2b2b2b")

            if chart_type == "bar":
                axes.bar(x_values, y_values, color="#1f9d55")
                axes.set_xlabel(x_label, color="white")
                axes.set_ylabel(y_label, color="white")
                axes.tick_params(axis="x", rotation=30)
                axes.grid(
                    axis="y",
                    linestyle="--",
                    alpha=0.25,
                )

            elif chart_type == "line":
                axes.plot(
                    x_values,
                    y_values,
                    marker="o",
                    linewidth=2,
                    color="#3b82f6",
                )
                axes.set_xlabel(x_label, color="white")
                axes.set_ylabel(y_label, color="white")
                axes.tick_params(axis="x", rotation=30)
                axes.grid(
                    axis="y",
                    linestyle="--",
                    alpha=0.25,
                )

            else:
                axes.pie(
                    y_values,
                    labels=x_values,
                    autopct="%1.1f%%",
                    startangle=90,
                )

            axes.set_title(title, color="white", pad=12)
            axes.tick_params(colors="white")
            for spine in axes.spines.values():
                spine.set_color("#666666")

            figure.tight_layout()

            canvas = FigureCanvasTkAgg(
                figure,
                master=self.chart_area,
            )
            canvas.draw()

            chart_widget = canvas.get_tk_widget()
            chart_widget.configure(
                highlightthickness=0,
                bd=0,
            )
            chart_widget.pack(
                fill="both",
                expand=True,
            )

            self._chart_figure = figure
            self._chart_canvas = canvas
            self.chart_title.configure(text=f"📊 {title}")

            self.chart_frame.pack(
                fill="x",
                padx=10,
                pady=(0, 10),
                before=self.input_frame,
            )

        except Exception:
            self._clear_chart()
            self._append_message(
                "系统",
                "图表生成失败，已保留文字结果。",
            )

    def _pick_file(self):
        """弹出文件选择器，把选中的文件路径填入输入框"""
        file_path = filedialog.askopenfilename(
            title="选择数据文件",
            filetypes=[
                ("数据文件", "*.xlsx *.xls *.csv *.json *.txt *.md"),
                ("所有文件", "*.*"),
            ]
        )

        if file_path:
            # 把路径替换成正斜杠，避免反斜杠被模型误认为转义字符
            file_path = file_path.replace("\\", "/")
            # 路径填入输入框，用户可继续补充说明（如表名）再发送
            self.input_box.delete("1.0", END)
            self.input_box.insert("1.0", f'把 "{file_path}" 导入数据库')

    def _pick_folder(self):
        """弹出文件夹选择器，把路径填入输入框"""
        folder_path = filedialog.askdirectory(title="选择数据文件夹")
        if folder_path:
            folder_path = folder_path.replace("\\", "/")
            self.input_box.delete("1.0", END)
            self.input_box.insert(
                "1.0",
                f'把 "{folder_path}" 文件夹里的所有数据文件批量导入数据库'
            )

    def _send_message(self):
        """发送消息"""
        user_input = self.input_box.get("1.0", END).strip()
        if not user_input:
            return

        self.input_box.delete("1.0", END)
        self._append_message("你", user_input)

        threading.Thread(target=self._get_response, args=(user_input,), daemon=True).start()

    def _get_response(self, user_input):
        try:
            def on_event(text):
                self.root.after(0, self._append_message, "⚙️", text)

            result = self.agent.chat(user_input, on_event=on_event)
            self.root.after(0, self._render_agent_result, result)

        except Exception as exc:
            self.root.after(0, self._append_message, "系统", f"错误: {exc}")

    def _render_agent_result(self, result):
        # 每次新结果先清理上一张图表
        self._clear_chart()

        # 正常完成：优先使用 LangChain 结构化输出
        if result.status == "completed":
            if result.structured_response is not None:
                structured = result.structured_response

                # 兼容 Pydantic 模型和 dict
                data = (
                    structured
                    if isinstance(structured, dict)
                    else structured.model_dump()
                )

                answer_text = data.get("answer") or result.content
                answer_type = data.get("answer_type", "general")

                explanation = data.get("explanation") or []
                if isinstance(explanation, str):
                    explanation = [explanation]

                if explanation:
                    explanation_text = "\n".join(
                        f"- {item}"
                        for item in explanation
                    )
                    self._append_message(
                        "结果解释",
                        explanation_text,
                    )

                # 展示本次查询的指标信息
                meta_lines = []
                if data.get("metric_name"):
                    meta_lines.append(f"指标：{data['metric_name']}")
                if data.get("metric_id"):
                    meta_lines.append(f"指标编码：{data['metric_id']}")
                if data.get("time_range"):
                    meta_lines.append(f"时间范围：{data['time_range']}")
                if data.get("dimensions"):
                    meta_lines.append(
                        "分析维度：" + "、".join(data["dimensions"])
                    )

                if meta_lines:
                    self._append_message("查询信息", "\n".join(meta_lines))

                # 展示工具实际返回的数据行
                rows = data.get("rows") or []
                if rows:
                    self._append_message("数据", self._format_rows(rows))

                # 展示业务口径和限制
                caveats = data.get("caveats") or []
                if caveats:
                    caveat_text = "\n".join(f"- {item}" for item in caveats)
                    self._append_message("口径说明", caveat_text)

                # 根据真实数据行自动推断并渲染图表
                chart = self._infer_chart_spec(
                    rows,
                    metric_name=data.get("metric_name"),
                    time_range=data.get("time_range"),
                )
                if chart:
                    self._render_chart(rows, chart)

                return

            # 没有结构化输出时，退回普通文本
            self._append_message("AI", result.content)
            return

        # 人工审核：per_call 模式建议一次处理一个 interrupt
        if not result.interrupts:
            self._append_message("系统", "Agent 进入中断状态，但没有收到审核信息。")
            return

        review = result.interrupts[0]

        review_text = (
            "查询需要人工确认\n\n"
            f"工具：{review.get('tool')}\n"
            f"参数：{json.dumps(review.get('arguments'), ensure_ascii=False, indent=2)}\n\n"
            f"{review.get('description', '')}"
        )

        approved = messagebox.askyesno("人工审核", review_text)

        threading.Thread(
            target=self._resume_review,
            args=(
                review["interrupt_id"],
                "approve" if approved else "reject",
            ),
            daemon=True,
        ).start()

    @staticmethod
    def _format_rows(rows: list[dict]) -> str:
        if not rows:
            return ""

        headers = []
        for row in rows:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)

        lines = [" | ".join(headers)]

        for row in rows:
            lines.append(
                " | ".join(
                    str(row.get(header, ""))
                    for header in headers
                )
            )

        return "\n".join(lines)

    def _resume_review(self, interrupt_id, decision):
        try:
            def on_event(text):
                self.root.after(0, self._append_message, "⚙️", text)

            result = self.agent.resume(
                interrupt_id=interrupt_id,
                decision=decision,
                message=(
                    None
                    if decision == "approve"
                    else "用户拒绝执行，请缩小查询范围或调整参数。"
                ),
                on_event=on_event,
            )
            self.root.after(0, self._render_agent_result, result)

        except Exception as exc:
            self.root.after(0, self._append_message, "系统", f"错误: {exc}")

    def _on_enter(self, event):
        """回车发送"""
        self._send_message()
        return "break"

    def run(self):
        """运行"""
        self.root.mainloop()


if __name__ == "__main__":
    app = ChatApp()
    app.run()
