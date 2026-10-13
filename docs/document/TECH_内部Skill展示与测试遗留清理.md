# 内部 Skill 展示与测试遗留清理

## 已确认行为

10 个小红书内部 Skill 从普通聊天选择器、推荐、自动发现和新手动绑定中排除，保留后台正常调用。用户图片入口不变；个人创建的 Skill 不根据同名 key 统一隐藏。管理后台仍可维护内部 Skill。

内部名单：easel-competitor-analysis、easel-content-gap-analysis、easel-topic-evaluator、easel-content-calendar、easel-xhs-note-creator、easel-text-polisher、easel-quality-gate、easel-risk-scanner、easel-card-design、easel-card-xiaohongshu。

测试名单：a001、asset-test-0921、recommendation-smoke-20260926、test-daily-report。四者均从新任务发现中排除；迁移只关闭后三者的组织启用标记。a001 保留授权，以兼容已有版本锁定任务。

## 实现

`discovery_policy.py` 集中维护服务器拥有的业务边界；`SkillResolver.select` 对当前执行上下文统一过滤，覆盖聊天目录、推荐、手动选择和后台新任务配置。小红书内部方法允许 scheduled/preflight，测试方法不出现在新任务目录。

已有聊天会话固定项在新轮次中跳过隐藏技能，避免因隐藏而阻塞普通对话。已有执行快照恢复使用 restoring 路径，仅跳过新增的发现过滤，仍检查身份、组织、权限、功能开关、发布状态、授权和内容哈希。

没有改写已发布正文、元数据或哈希，没有切换小红书现有版本授权。这样现有后台任务锁定的快照可以原样使用。10 份新导入模板默认 execution_modes=[scheduled]、model_selectable=false；普通用户无需选择这些后台步骤。

用户入口保留：电商主图策划与生成、电商套图策划、参考图多方案提示词、淘宝商品白底图；后者仍遵循原有图生图模式限制。

## 发布与回退

发布包含 `300_disable_retired_test_skills.sql`。迁移仅将指定三个组织测试包的 enabled 设为 false，不删除包、版本或历史数据，不修改个人方法。临时 RLS 策略仅允许迁移所有者处理指定对象，迁移结束全部移除；迁移重复执行安全。

回退代码可恢复原发现规则；测试遗留的关闭标记不自动恢复。如确需重新测试，应在管理后台审核有效版本后按正常流程启用，不直接恢复已废弃版本的启用标记。

## 验证记录

2026-10-13：190 项定向测试通过，无跳过。

- 173 项：发现隔离、用户入口、个人方法、会话绑定、推荐、运行时、后台快照及社媒方法测试。
- 1 项真实 PostgreSQL 测试：非超级用户迁移所有者、强制 RLS、两个组织、精确范围、重复执行、正文版本不变、临时策略清除。
- 16 项真实 PostgreSQL/NAS 后台测试：10 个真实小红书方法使用新旧元数据发布、绑定、恢复和后台目录可用，以及原有定时版本锁定回归。

未调用外部平台或付费模型；尚未提交部署。
