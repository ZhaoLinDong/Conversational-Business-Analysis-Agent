Demo 测试记录
1. 测试环境
- 操作系统：Windows
- Python：3.13.9
- LangChain：1.4.9
- LangChain Core：1.7.6
- LangGraph：1.2.20
- 模型：`qwen-plus`
- 数据库：`Database/ecommerce.db`
- 测试日期：2026-11-10
测试方法：
- 工具直接调用测试；
- 无头 Agent 对话测试；
- 只读数据库结果核对；
- 守卫函数独立测试；
- 不执行未授权数据库写入。
2. 公开练习题 Demo
数据来源：数据库 练习题 表。
  编号           用户问题                                                         标准答案                                                   实际结果                      状态
  Q01            2024年1月至2025年12月共有多少笔已送达订单？                      10714                                                      10714                         通过

  Q02            2024年1月至2025年12月已送达订单的商品销售额是多少？不含运费。    4858920.84 元                                              4858920.84 元                 通过

  Q03            4月商品销售额较3月增长多少？                                     15.00%                                                     15.00%                        通过

  Q04            4月商品销售额最高的品类是什么？金额是多少？                      数码家电，105245.50 元                                     数码家电，105245.50 元        通过

  Q05            5月已送达订单不少于10笔的商家中，延迟率最高的是谁？              广州美妆个护商家004，91.53%                                S004                          通过
广州美妆个护商家004，91.53%   

  Q06            有评价的已送达订单中，延迟订单与准时订单的平均评分分别是多少？   延迟 2.00；准时 4.02                                       延迟 2.00；准时 4.02          通过

  Q07            有多少自然客户完成了至少2笔已送达订单？                          2378                                                       2378                          通过
Q08            直接关联商品明细和支付表为什么会导致金额重复？                   两张表对同一订单都可能有多行，应分别按订单汇总后再关联。   已正确解释一对多 JOIN         通过
导致行数膨胀，并说明应先按
order_id 聚合再关联
Q08 Demo 回答摘要
order_items 和 order_payments 都与 orders 构成一对多关系。
直接把两者 JOIN 到 orders 会产生行数放大，
导致商品金额和支付金额按对方明细行重复计算。
正确做法是先分别按 order_id 聚合，再关联聚合结果。
3. 自行补充测试
  编号           测试场景                                    预期                     实际                                       状态
  T01            2025年整体延迟率                            1427 / 5737 = 24.87%     24.87%                                     通过

  T02            2025年商品销售额最高商家，并给出            S026，43549.60，62 单    S026，43549.60，62 单                      通过
seller_id、商家名称、销售额、已送达订单数                                                                       

  T03            单 CTE 查询                                 正常执行                 返回订单商品销售额                         通过

  T04            多 CTE 预聚合后关联 order_items /           正常执行                 返回 2025 年商品销售额和支付金额           通过
order_payments                                                                                                  

  T05            标准时间问题使用 delivered_at 做月份过滤    拒绝                     SQL_SEMANTIC_ERROR：时间字段漂移           通过

  T06            自然客户问题只使用 customer_id              拒绝                     SQL_SEMANTIC_ERROR：客户粒度错误           通过

  T07            order_items 和 order_payments 同层直接 JOIN 拒绝                     要求先按 order_id 聚合                     通过

  T08            DELETE 写操作                               拒绝                     SQL_INVALID：只允许 SELECT / WITH SELECT   通过

  T09            SQL 返回行数超过 max_rows                   截断并标记               rows_shown=1，rows_truncated=true          通过
rows_truncated                                                      

  T10            SQL 长查询超时                              返回 SQL 超时            TimeoutError：SQL 执行超过 0.1 秒          通过

  T11            净利润问题                                  拒绝计算                 Scope Router：data_result + refuse         通过

  T12            退款损失问题                                拒绝计算                 Scope Router：data_result + refuse         通过

  T13            道路距离问题                                拒绝计算                 Scope Router：data_result + refuse         通过

  T14            因果问题                                    允许回答但限制因果结论   Scope Router：answer_with_caution          通过

  T15            数据导入请求                                识别为 data_import       intent=data_import，response_mode=answer   通过（未执行实际写库）

  T16            空回复流                                    返回可读兜底提示         模型未返回有效结果，请稍后重试......       通过

  T17            标准指标 Q01---Q07                          正确                     全部正确                                   通过

  T18            图表推断 Q04                                多行分类柱状图           x_field=品类名称，y_field=商品销售额       通过

  T19            图表推断 Q05                                单商家单指标柱状图       x_field=商家名称，y_field=延迟率           通过
T20            图表推断 Q06                                单行双指标对比柱状图     x_field=对比项，y_field=数值               通过
4. 关键链路验证
4.1 标准指标链路
search_business_metrics
→ query_business_metric
→ MetricCompiler
→ validate_plan
→ render_sql
→ read-only SQLite
已验证：
- 标准指标加载正常；
- 指标范围守卫正常；
- 结果解释字段正常；
- 结构化回答正常。
4.2 通用查询链路
search_business_schema
→ run_readonly_sql
→ SQL 语义守卫
→ read-only SQLite
已验证：
- 相关表、字段、JOIN 能正确检索；
- CTE 和预聚合查询可执行；
- 时间字段、客户粒度和一对多 JOIN 守卫能拦截错误；
- 时间字段被拦截后，Agent 能自动修正 SQL 并重试。
4.3 连续追问
已验证：
- 同一 thread_id 能保持上下文；
- 追问可以沿用上一轮数据；
- 不需要每次重新启动查询；
- 用户要求"画图"时可以基于上一轮 rows 生成图表规格。
5. 已知限制
- 通用 SQL 语义守卫仍是保守规则，不完全等价于 SQL AST 级语义分析；
- 复杂多表、多指标组合仍可能出现口径或 fan-out 问题；
- 因果问题只能描述相关性和限制，不能进行真正因果推断；
- 预测任务只支持历史趋势描述，不提供可靠预测；
- 数据导入测试只验证了分类和工具调用路径，未在生产数据上重复执行写入；
- 图表自动生成以二维分类/数值数据为主，暂不支持复杂多系列、坐标轴联动和交互式图表；
- 未指定年份的月份问题仍依赖上下文和 Scope Router
  解释，建议在业务上明确年份或采用固定默认年份策略。