"""PromptBuilder 三层结构。

- static_layer:  Layer 1 — 永久不变内容（角色 / 规则 / 工作流 / 工具策略 / 模式）
- dynamic_layer: Layer 2 — 每次变化内容（时间 / 偏好 / persona / 相关记忆）
- user_layer:    Layer 3 — 用户消息层（附件 XML + user text）
"""
