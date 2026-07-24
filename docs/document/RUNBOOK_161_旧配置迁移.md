# RUNBOOK 161：旧配置原子迁移

> 状态：158–161 已部署生产；162 ACL 合同与正式导入待执行
> 适用：`org_configs`、`organizations.encrypt_key`、快麦外部 Cookie
> 不包含：消费者切换、旧表删除、Reader 能力清理

## 1. 安全边界

本步骤只把旧配置复制到新 Registry/Secret 控制平面。业务消费者仍读取旧配置，因此：

- 成功导入不等于切换完成。
- 任一步异常都停止推进，旧配置继续作为业务真相源。
- 不删除或修改旧配置。
- 不删除 `configuration_import_audit_log`。
- 不手工修改 `schema_migration_ledger`。
- 成功导入后不直接执行 161 rollback。
- Reader、migrator、管理员和运行服务不得复用数据库 URL。
- `.env.legacy-config-import` 不得加入任何 Systemd `EnvironmentFile`。

## 2. 变更窗口前置

开始前必须留存：

- 当前 Git commit SHA。
- PostgreSQL 全库备份位置、完成时间、校验结果和恢复负责人。
- 数据库管理员、迁移执行人、复核人。
- 维护窗口开始/结束时间。
- 生产服务健康基线。
- 当前迁移账本导出。

前置结构顺序：

1. 150–157 角色、所有权、RLS 和治理能力已经按各自 Runbook 验证。
2. 使用管理员脚本建立 export definer 单表 ACL。
3. 使用标准 Migration Runner 应用 158–162。
4. 不执行消费者切换，不运行角色最终收口。

## 3. 一次性环境文件

从模板创建仅供人工迁移进程使用的文件：

```bash
install -m 600 \
  deploy/env-templates/legacy-config-import.env.template \
  /var/www/everydayai/backend/.env.legacy-config-import
```

填写真实值后确认：

```bash
stat -c '%a %n' \
  /var/www/everydayai/backend/.env.legacy-config-import
```

必须满足：

- 权限为 `600`。
- `LEGACY_CONFIG_SOURCE_DATABASE_URL` 使用
  `everydayai_config_import_reader`。
- `MIGRATION_DATABASE_URL` 使用 `everydayai_migrator`。
- 两个 URL 不相同。
- `ORG_CONFIG_ENCRYPT_KEY` 只作为企业旧密钥缺失时的显式兜底。
- KEK keyring 至少包含 current version，值为 base64 编码的 32 字节密钥。
- 文件内没有模板占位符。

加载方式：

```bash
set -a
source /var/www/everydayai/backend/.env.legacy-config-import
set +a
```

不要打印环境变量，不使用 `set -x`。

## 4. 建立管理员 ACL 并应用数据库迁移

先由独立数据库管理员建立 export definer 的单表只读权限：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
  bash /var/www/everydayai/deploy/grant-legacy-config-export-access.sh
```

然后进入后端目录并使用标准 Runner：

```bash
cd /var/www/everydayai/backend
python scripts/migration_runner.py plan --applied-by config-import-161
python scripts/migration_runner.py apply --applied-by config-import-161
python scripts/migration_runner.py check --applied-by config-import-161
```

计划中必须保持：

```text
158_configuration_control_plane_foundation.sql
159_configuration_management_core.sql
159_configuration_management_facades.sql
160_configuration_resolution_core.sql
160_configuration_resolution_facades.sql
161_configuration_legacy_import.sql
162_configuration_legacy_export_access.sql
```

同编号迁移必须保持完整文件名顺序，禁止手工执行片段 SQL。

## 5. 数据库只读 preflight

管理员 URL 只在当前人工终端注入：

```bash
TENANT_DB_ADMIN_URL='postgresql://...' \
  bash /var/www/everydayai/deploy/preflight-legacy-config-import.sh
```

成功输出必须为：

```text
✅ 旧配置导入数据库只读前置检查通过
```

该脚本验证：

- 158–162 台账完整。
- Reader、migrator 和运行角色属性正确。
- export/import RPC owner 与 ACL 正确。
- export definer 可读取三张固定旧源表。
- Reader 无旧表直读权。
- 四张敏感表启用 FORCE RLS。
- Registry 为 15 个配置定义、11 个固定 Bundle。
- 新配置、策略、Secret 和导入审计全部为空。

失败时停止，不运行 dry-run。

## 6. 生成固定 import_id

生成一次并记录到变更单：

```bash
IMPORT_ID=$(
  python -c 'from uuid import uuid4; print(uuid4())'
)
printf 'import_id=%s\n' "$IMPORT_ID"
```

dry-run 与 apply 必须使用同一个 `IMPORT_ID`。

## 7. 执行 dry-run

```bash
python scripts/migrate_legacy_configuration.py \
  --import-id "$IMPORT_ID"
```

dry-run 只允许输出：

- `mode=dry-run`
- `import_id`
- 企业数
- 目标条目数
- 配置键名
- apply 确认字符串

复核清单：

- 命令退出码为 0，且没有输出 blocked preflight 结果。
- 企业数量与生产组织数量一致。
- 配置键集合与旧配置用途一致。
- expired/invalid 快麦凭证没有生成新配置。
- 输出不包含 API Key、Token、Cookie、旧密钥或 envelope。

任何 `LEGACY_IMPORT_*` 错误都停止推进。修复旧数据前必须另开任务并重新审计，
不得在迁移脚本里放宽校验。

## 8. 双人确认

执行人与复核人共同确认：

- 数据库 preflight 成功。
- dry-run 的 `IMPORT_ID`、企业数、条目数和键集合已记录。
- 备份可用。
- source 与 migrator URL 不同。
- 当前消费者仍使用旧配置。

确认字符串必须精确为：

```text
APPLY:<IMPORT_ID>
```

## 9. 原子 apply

```bash
python scripts/migrate_legacy_configuration.py \
  --apply \
  --import-id "$IMPORT_ID" \
  --confirm "APPLY:$IMPORT_ID"
```

成功响应中的 `database_result` 必须同时满足：

- `import_id` 等于记录值。
- `imported_count` 等于 dry-run 的目标条目数。
- `version` 等于 `1`。

RPC 使用 create-only CAS。任一目标已存在、输入非法或权限错误都会回滚整个事务。

## 10. 导入后只读验证

使用安全管理员 psql 启动器执行以下查询。将占位符替换为已记录值，但不要把数据库
URL、密钥或 Secret 写入 SQL 文件。

```sql
-- A. 本次审计数量
SELECT import_id, COUNT(*) AS audit_count,
       MIN(imported_version) AS min_version,
       MAX(imported_version) AS max_version
  FROM configuration_import_audit_log
 WHERE import_id = '<IMPORT_ID>'::UUID
 GROUP BY import_id;

-- B. 每条审计都存在对应 active version=1 配置
SELECT COUNT(*) AS audit_count,
       COUNT(entry.id) AS matched_entry_count,
       COUNT(*) FILTER (
           WHERE entry.status = 'active' AND entry.version = 1
       ) AS active_v1_count
  FROM configuration_import_audit_log audit
  LEFT JOIN configuration_entries entry
    ON entry.scope_kind = 'organization'
   AND entry.org_id = audit.org_id
   AND entry.config_key = audit.config_key
 WHERE audit.import_id = '<IMPORT_ID>'::UUID;

-- C. Secret 定义必须关联 active payload_version=1 Secret
SELECT COUNT(*) AS invalid_secret_count
  FROM configuration_import_audit_log audit
  JOIN configuration_entries entry
    ON entry.scope_kind = 'organization'
   AND entry.org_id = audit.org_id
   AND entry.config_key = audit.config_key
  JOIN configuration_definitions definition
    ON definition.definition_version = entry.definition_version
   AND definition.config_key = entry.config_key
  LEFT JOIN secret_records secret ON secret.id = entry.secret_id
 WHERE audit.import_id = '<IMPORT_ID>'::UUID
   AND definition.contract_json->>'value_kind' = 'secret'
   AND (
       secret.id IS NULL
       OR secret.status <> 'active'
       OR secret.payload_version <> 1
   );

-- D. 不得出现其他 import_id
SELECT COUNT(DISTINCT import_id) AS import_batch_count
  FROM configuration_import_audit_log;
```

判定标准：

- A：`audit_count` 等于 dry-run/apply 数量，min/max version 都是 1。
- B：三个计数完全相等。
- C：`invalid_secret_count=0`。
- D：`import_batch_count=1`。
- 旧表行数和内容未变化。
- 所有业务服务仍健康，且没有切换到新 Bundle。

验证结果只记录计数、键名、版本和状态，不记录普通配置值、密文或 envelope。

## 11. 失败与回退矩阵

| 阶段 | 处理 |
|---|---|
| 迁移应用前失败 | 停止；生产保持原状 |
| 158–162 应用后、dry-run 前失败 | 保留未使用的新能力；不要手改台账 |
| dry-run 失败 | 停止；新目标仍为空，旧配置继续服务 |
| apply 返回失败 | 确认审计和目标仍为空；事务已整体回滚 |
| apply 成功、验证通过 | 保留旧真相源，进入下一独立阶段 |
| apply 成功、验证异常 | 冻结推进；不删除新数据或审计，继续使用旧消费者并专项调查 |
| 数据库级灾难 | 由数据库负责人按已验证备份执行整库恢复 |

成功导入后的“业务回退”是保持消费者读取旧配置，不是逆向覆盖、删除新 Secret 或删除
审计。恢复整库属于独立高风险操作，必须重新审批。

## 12. 收尾

- 保存变更单、Git SHA、备份证据、import_id、dry-run/apply 脱敏输出和验证计数。
- 迁移环境文件不得进入代码仓库、日志附件或 Systemd。
- 不删除旧配置、不撤销旧消费者权限。
- 不立即执行 Reader 角色/RPC 清理；清理属于消费者切换后的独立迁移。
- 下一阶段只能在本 Runbook 全部通过后开始。
