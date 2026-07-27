# 快麦凭证保存修复技术设计

## 问题与目标

生产请求两次在 `POST /api/admin/kuaimai/credentials` 返回 400，未进入配置写入。
界面原先允许复制“任意 XHR”，但保存契约要求 cURL 同时包含 `companyid` 与
`_censeid`。目标是恢复“有效材料才保存、保存结果可回读、失败原因可操作”的约束。

## 调用链与职责

1. `KuaimaiSourcesTab` 引导管理员复制当前数据源的报表查询请求，并展示统一错误消息。
2. `create_credential` 校验 cURL、精确快麦主机、必需字段和数据源一致性。
3. `ExternalConfigurationControl.set` 继续调用
   `runtime_set_external_configuration`，原子写入 Cookie 与 companyid。
4. 控制面重新解析对应 Bundle；只有回读成功，接口才返回已保存。

## 失败行为

- 非法 cURL、错误主机、缺少字段、来源冲突：返回稳定的 400 业务错误码，不写配置。
- 配置 RPC 或回读失败：返回 `KUAIMAI_CONFIG_SAVE_FAILED`（503），日志只记录
  org/source/user 上下文，不记录 Cookie 或 cURL。
- 重复提交由前端保存状态阻止；数据库版本锁负责并发冲突和整体回滚。

## 兼容与回滚

不修改数据库 Schema、配置键或现有 API 成功响应。旧的标准 Chrome cURL 继续可用，
新增 Safari ANSI-C 引号及 `--header=`、`--cookie=` 形式。回滚只需恢复应用代码，
已经通过配置控制面保存的 Bundle 无需迁移。

## 验证

- 解析格式、精确主机、缺字段和来源冲突单元测试。
- 成功保存参数、回读结果和控制面失败映射测试。
- 前端统一错误转换测试、ESLint、TypeScript 与生产构建。
- 部署后以脱敏的真实报表请求验证保存、列表回显和连接测试。
