# 统一配置与 Secret 控制平面：迁移附录

> 主设计：`TECH_统一配置与Secret控制平面.md`
> 生产执行：`RUNBOOK_161_旧配置迁移.md`

## 1. 迁移顺序

当前 158/159 未进生产，停止接线并重新编号其职责：

1. 修正 156/157 active 企业授权顺序并补真实角色矩阵。
2. 158：固化 Registry 定义投影，创建 configuration_entries/policies、secret_records、FORCE RLS。
3. 159：实现信封加密 Provider、管理写和状态读能力。
4. 160：实现有效配置解析与固定 Bundle capabilities。
5. 161：先以无秘密值预检验证旧键组合、Corp ID 来源和 Cookie 加密/状态，再迁移
   `org_configs` 和 `organizations.encrypt_key`；旧表仍为真相源。
6. 162：迁移 Kuaimai External Cookie，消除旧明文兼容。
7. 163：切换所有运行消费者和 Adapter BYOK，观察无旧读取后切新真相源。
8. 164：平台/企业/个人 API 与 UI、锁定策略和审计投影。
9. 165：Skill Registry、企业共享、平台推荐安装和 SecretRef。
10. 撤销旧 `org_configs`/encrypt_key 运行权限，删除全局回退；观察期后再删除旧列/表。

每一步独立 apply/rollback/reapply；生产不做无流量灰度，测试通过后按维护窗口整体切换该步。

## 2. 生产审计结论

生产只读审计确认当前线上仍是单一 `everydayai` 角色，150–160 尚未应用，新配置表、
服务角色、RLS 和 KEK 环境均不存在。生产旧配置无空值或孤儿记录，但 Corp ID 同时存在
组织列与旧加密键，迁移前必须在内存中验证一致；快麦外部凭证已加密但均为 expired，
迁移后不得自动恢复 active。

## 3. 161 分阶段证据

161.2 采集器固定批量读取 organizations、org_configs 和 kuaimai_external_credentials，
不接受调用方提供任意表名或字段。旧明文仅存在于当前函数局部变量，不进入报告、日志或
异常；企业密钥缺失时才使用显式注入的旧全局密钥。损坏密文、未知键、孤儿/畸形记录均
阻断整个企业迁移。

161.3A 数据库导入面只授权 everydayai_migrator，并同时要求事务设置
`app.legacy_config_import=apply`。调用方一次提交完整目标数组，函数逐项使用版本 0 CAS；
PostgreSQL 函数调用本身构成单一事务边界，任何冲突都会撤销整批。审计表只保存 import_id、
企业、键、版本和来源，不保存普通配置值、envelope 或旧材料，并从创建起启用 FORCE RLS。

161.3B1 转换计划要求输入组织集合与预检报告精确一致，并在生成任何导入项前确认全部
企业可迁移。旧 Secret 按 Registry 固定 payload 组合后立即转换为 payload_version=1
envelope；计划摘要和对象表示不包含旧明文或 envelope。外部凭证只有 active 状态生成
Cookie/company_id 两项，expired/invalid 保持未配置；缺少 Registry 必需的 `cookie_full`
在预检阶段即判为 incomplete。

161.3B2 最初使用独立 source DSN 读取三张旧表并生成预检和转换输入。后续实现审查确认
连接池三次查询不等于 PostgreSQL 一致性快照，因此 161.3C1 增加一次性
`everydayai_config_import_reader` 与 owner-held `export_legacy_configuration_snapshot`。
Reader 不继承 owner 且无旧表 SELECT；RPC 以 session_user + read GUC 双门禁固定返回
三数组，migrator 即使继承 owner 也无法通过 session_user 门禁。161.3C2 已将 Python
source 接到同一只读事务的 `SET LOCAL + export RPC`，并拒绝 source/migrator 复用
同一 URL。默认 CLI 只执行 dry-run；apply 还必须提供固定 import_id、
精确 `APPLY:<import_id>` 确认和独立 migrator DSN。执行器在同一 psycopg 事务/游标先验证
session_user，再执行 `SET LOCAL app.legacy_config_import='apply'` 与批量 RPC。真实
PostgreSQL 首轮测试发现 JSON 输入的 `null` 不是 SQL NULL，161 RPC 已使用
`NULLIF(..., 'null'::JSONB)` 修正；随后门禁测试发现缺失自定义 GUC 时 SQL NULL
比较会绕过 `<>`，已改用 `IS DISTINCT FROM 'apply'`。最终隔离 PostgreSQL 验证覆盖：
缺失 GUC 拒绝、普通值/Secret 同批成功、审计计数，以及重复 create-only 冲突整批回滚。

161.4.1 生产数据库 preflight 只允许独立管理员身份运行，并在 `SET TRANSACTION READ
ONLY` 中验证迁移台账、角色属性、RPC 授权、FORCE RLS、Registry 固定计数和全空导入
目标；脚本末尾固定 ROLLBACK。旧值解密一致性仍由随后使用独立只读 DSN 的 Python
dry-run 负责，数据库 preflight 不读取或输出任何配置材料。隔离 PostgreSQL 已验证
脚本真实执行通过且目标计数不变；PUBLIC 函数授权使用 PostgreSQL 固定 grantee OID 0
检查，避免将 `PUBLIC` 错当作普通登录角色。

161.4.2 生产执行顺序已固化在 `RUNBOOK_161_旧配置迁移.md`：Migration Runner 应用
158–161 后，先做数据库只读 preflight，再以同一 import_id 执行 dry-run 与双人确认的
原子 apply，最后只读核对审计、active version=1 配置、Secret payload_version=1 和
单一导入批次。成功导入不切换消费者；异常时保持旧配置为业务真相源，禁止删除新数据、
持久审计或手工修改迁移账本。
