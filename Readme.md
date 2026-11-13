# 云账房对话式商业智能 Agent

本项目是一个面向电商经营数据分析的对话式智能体。用户可以通过自然语言提出业务问题，Agent 根据业务数据字典、指标定义和查询工具完成数据查询与分析，并在桌面界面中展示结果。

项目主要使用 LangChain 构建 Agent 和工具调用流程，结合 LangGraph 实现会话检查点与人工审核后的执行恢复。

注：本项目基于云账房赛题，因此该仓库不包含赛题商业数据与本人编写的业务数据说明表。请根据实际需求准备业务数据和数据说明表。

## 一、项目功能

- **自然语言查询**：根据用户提出的业务问题，生成查询计划并查询 SQLite 业务数据库。
- **业务指标查询**：支持检索和查询项目中定义的业务指标，如商品销售金额、复购客户数等。
- **业务数据结构检索**：查询数据表、字段及其业务含义，辅助完成业务问题分析。
- **多表数据查询**：结合业务元数据处理数据表、字段和关联关系。
- **数据导入**：支持导入 Excel、CSV、JSON 和文本等文件，也支持批量导入文件夹中的表格数据。
- **人工确认**：导入数据等涉及数据库写入的操作，需要用户确认后执行。
- **桌面交互**：通过桌面界面输入问题、查看 Agent 回答，并在适合的场景下展示图表。
- **会话管理**：支持在同一会话中连续交流，并使用检查点维护会话状态。
- **异常与调用控制**：设置模型重试、工具异常处理和模型及工具的调用次数限制。

## 二、技术栈

| 部分 | 技术 |
| --- | --- |
| Agent 与模型调用 | LangChain、LangChain OpenAI |
| 会话检查点与中断恢复 | LangGraph |
| 数据存储与处理 | SQLite、Pandas、OpenPyXL |
| 桌面界面与图表 | CustomTkinter、Tkinter、Matplotlib |
| 数据结构校验 | Pydantic |

## 三、项目结构

```text
yunzhangfang/
├── config.py                    # 模型配置、系统提示词和数据库路径
├── requirements.txt             # Python 依赖
├── Readme.md                    # 项目说明
├── gui/
│   └── gui_app.py               # 桌面界面启动入口
├── Model_core/
│   ├── Agent_with_tools.py      # Agent、工具和中间件装配
│   └── stream_events.py         # Agent 流式事件处理
├── Memory/                      # 记忆、消息裁剪与会话检查点
├── tools/                       # Langchain框架的工具，包括数据导入、业务查询和 SQL 工具
├── rules/                       # 护栏模块   
│   ├── metadata/                # 业务目录、查询元数据和指标定义
│   ├── query_plan.py            # 查询计划及校验
│   ├── metric_compiler.py       # 指标查询编译
│   └── business_guard.py        # 人工审核与异常处理
├── docs/                        # 业务数据字典和说明
├── Database/                    # 默认业务数据库目录
```

## 四、运行环境

- Python 3.10 或以上
- Windows 桌面环境，或支持 Tkinter 图形界面的其他系统
- 可访问的、兼容 OpenAI 接口格式的模型服务
- 具备工具调用和结构化输出能力的模型
- 与项目业务字典和指标定义匹配的电商业务数据

## 五、安装依赖

在项目根目录打开终端，执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果使用 Linux 或 macOS，可通过以下命令激活虚拟环境：

```bash
source .venv/bin/activate
```

## 六、模型配置

打开项目根目录下的 `config.py`，配置模型服务：

```python
API_KEY = "在这里填写 API Key"
BASE_URL = "在这里填写模型服务的接口地址"
MODEL_NAME = "在这里填写模型名称"
```

其中：

- `API_KEY`：模型服务提供的 API Key。
- `BASE_URL`：模型服务兼容 OpenAI 格式的接口地址。
- `MODEL_NAME`：用于查询计划生成等功能的模型名称。

### 修改桌面界面使用的模型

打开 `gui/gui_app.py`，找到：

```python
self.agent = ToolAgent(model_name="qwen-plus")
```

将 `qwen-plus` 修改为实际使用的模型名称，例如：

```python
self.agent = ToolAgent(model_name="实际模型名称")
```

**注意：** `config.py` 中的 `MODEL_NAME` 和 `gui/gui_app.py` 中的模型名称需要分别检查。

项目当前直接从 `config.py` 读取 API Key、接口地址和模型名称。

## 七、准备业务数据

项目默认数据库路径为：

```text
Database/ecommerce.db
```

需要确保该路径下存在业务数据库，或通过程序的数据导入功能将业务数据导入 SQLite。

业务数据表名、字段名及其含义应与以下文件中的定义保持一致：

- `docs/`：业务数据字典和说明。
- `rules/metadata/business_catalog.json`：业务数据目录。
- `rules/metadata/query_meta.json`：查询元数据。
- `rules/metadata/metrics/`：业务指标定义。

数据导入操作会写入数据库，执行时需要在界面中确认。

## 八、启动项目

在项目根目录执行：

```powershell
python gui/gui_app.py
```

启动后，在桌面界面中输入业务问题即可进行交互。

`main.py` 当前用于查询计划格式调试，不是桌面程序的启动入口。

## 九、使用

需要下载赛题资料并解压

查询范围、指标口径和可用结果以实际数据及项目中配置的业务规则为准。

## 十、处理流程

```text
用户提出问题
    ↓
Agent 判断问题并选择工具
    ↓
读取业务字典、指标定义或查询元数据
    ↓
生成并校验查询计划
    ↓
构造 SQL 并查询 SQLite 数据库
    ↓
整理查询结果，在界面中展示
```

涉及数据导入时，程序会暂停并等待用户批准或拒绝。

## 十一、注意事项

1. 请根据实际模型服务填写 API Key、接口地址和模型名称。
2. 请勿公开上传包含真实 API Key 的配置文件。
3. 查询结果依赖于模型、业务数据及指标定义，需要结合业务口径核对。
4. 项目默认使用内存检查点，程序退出后会话状态不会保留。代码提供可选的 SQLite 检查点，但需要在创建 Agent 时显式启用。
5. 项目包含只读 SQL 检查、业务范围约束和人工审核等机制，具体使用范围以代码实现为准。