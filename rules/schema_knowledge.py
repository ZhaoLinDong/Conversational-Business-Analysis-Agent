"""
业务数据字典
告诉模型每张表、每个字段的业务含义、别名和统计规则。
新增数据表时在 SCHEMA_KNOWLEDGE 里加一个条目即可。
"""

SCHEMA_KNOWLEDGE = {
    "orders": {
        "表说明": "订单主表，一条记录代表一次下单行为",
        "主键": "order_id",
        "字段说明": {
            "order_id": "订单编号，唯一标识，关联其他所有明细表",
            "customer_id": "下单客户标识，与订单一对一，不是自然人 id",
            "order_status": "订单状态枚举: delivered=已送达 / cancelled=已取消 / unavailable=无效订单 / shipped=已发货",
            "purchase_at": "下单时间，用户提交订单时间，统计订单量、销售额优先使用此时间",
            "approved_at": "审核通过时间，取消/无效订单该字段为空",
            "shipped_at": "发货时间，商家发出包裹时间，取消/无效订单为空",
            "delivered_at": "送达时间，用户签收时间，未送达/取消订单为空",
            "estimated_delivery_at": "预计送达时间，平台承诺送达时间，全部有值",
        },
        "业务规则": [
            "统计订单量：按 purchase_at 时间段统计 order_id 的数量",
            "延迟订单：delivered_at > estimated_delivery_at 的订单",
            "有效订单：order_status 不是 cancelled 且不是 unavailable",
            "取消/无效订单的 approved_at、shipped_at、delivered_at 为空，查询时注意判空",
        ],
        "业务别名": {
            "已送达": "order_status = 'delivered'",
            "已取消": "order_status = 'cancelled'",
            "已发货": "order_status = 'shipped'",
            "无效订单": "order_status = 'unavailable'",
            "下单时间": "purchase_at",
            "送达时间": "delivered_at",
            "延迟": "delivered_at > estimated_delivery_at",
        },
    },
    # 以后新增表在这里加，例如 "customers": {...}
}


def render_schema_knowledge() -> str:
    """把数据字典渲染成文本，注入 system_prompt"""
    lines = ["【数据字典】"]
    for table, info in SCHEMA_KNOWLEDGE.items():
        lines.append(f"\n■ 表 {table}: {info['表说明']}（主键: {info['主键']}）")
        lines.append("字段:")
        for field, desc in info["字段说明"].items():
            lines.append(f"  - {field}: {desc}")
        lines.append("业务规则:")
        for rule in info["业务规则"]:
            lines.append(f"  - {rule}")
        lines.append("业务术语对照:")
        for alias, mapping in info["业务别名"].items():
            lines.append(f"  - 用户说「{alias}」→ {mapping}")
    return "\n".join(lines)