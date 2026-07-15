# KIE 余额不足企业微信告警技术设计

> 状态：已确认，待实现
> 日期：2026-07-15

## 1. 目标与边界

KIE 返回 HTTP 402 或响应体 `code=402` 时，向平台 `super_admin` 对应的企业微信发送余额不足告警。覆盖图片、视频和聊天调用；30 分钟内跨用户、模型和任务只推送一次。备用供应商成功时仍提醒，暂不实现余额恢复通知。

不得改变现有异常传播、积分退款、Smart 重试、失败消息和 API 协议。告警失败不得阻断业务。

## 2. 现有能力

- `KieClient._handle_error_response()` 已将 402 转换为 `KieInsufficientBalanceError`。
- `core.error_alert_sink` 已提供 ERROR 日志消费、错误持久化、Redis 去重和企微管理员推送。
- 无需新增数据库、接口、环境变量或依赖。

## 3. 推荐数据流

```text
图片 / 视频 / 聊天请求
        ↓
KieClient 收到 HTTP 402 或响应体 code=402
        ↓
记录唯一结构化 ERROR：KIE_INSUFFICIENT_BALANCE
        ↓
继续抛出 KieInsufficientBalanceError
        ├─ 原业务链：退款、失败收尾、备用模型
        └─ ErrorSink：入库、固定指纹去重、企微推送
```

告警产生点只位于 `KieClient._handle_error_response()`；Adapter 和 Handler 不直接调用企业微信。

## 4. 告警内容与安全边界

告警包含环境、供应商、模型、错误码、影响、处理建议和时间。不包含 API Key、Token、用户 ID、提示词、文件 URL、完整上游响应以及 conversation/task/message ID。

## 5. 边界处理

| 场景 | 处理策略 |
|---|---|
| HTTP 402 | 识别并告警 |
| HTTP 200、响应体 code=402 | 同样识别 |
| KIE 文案变化 | 依赖状态码和异常类型，不依赖英文文案 |
| 多图或多用户并发失败 | 使用固定事件指纹，30 分钟只推一次 |
| 不同模型连续失败 | 企微全局去重，错误日志继续累计 |
| Smart 备用模型成功 | 仍保留 KIE 余额告警 |
| Redis 或企微不可用 | best-effort，不阻断业务 |
| 401、429、500、超时 | 不触发余额不足告警 |
| 查询任务缺少模型上下文 | 模型显示 `unknown` |

## 6. 修改范围

- `backend/services/adapters/kie/client.py`
  - 在 402 分支记录脱敏、结构化的余额事件。
  - 调用点在已有上下文可用时传递模型名。
- `backend/core/error_alert_sink.py`
  - 将固定余额事件纳入致命告警识别。
  - 为该事件生成跨模型、调用路径稳定的固定指纹。
- `backend/tests/test_kie_client_json.py`
  - 覆盖 HTTP 402、响应体 402、异常类型和脱敏日志。
- `backend/tests/test_error_alert_sink.py`
  - 覆盖余额事件识别、固定指纹和普通 KIE 错误不误报。
- `docs/CURRENT_ISSUES.md`
  - 实现完成后记录变更和验证证据。

不新增公共函数，不更新 `docs/FUNCTION_INDEX.md`；不新增、删除或移动文件之外的业务文件，不更新 `docs/PROJECT_OVERVIEW.md`。

## 7. 测试与验收

1. HTTP 402 和响应体 `code=402` 均抛出 `KieInsufficientBalanceError`。
2. 余额事件日志不包含完整响应和敏感字段。
3. 不同模型和动态信息生成同一个余额事件指纹。
4. 401、429、500 不触发余额不足告警。
5. 同批多条错误只形成一次企微推送输入。
6. 推送失败不影响原异常与退款链路。
7. 运行专项测试、相关回归、测试覆盖检查和完成后审查。

## 8. 部署与回滚

无数据库迁移、环境变量或 API 兼容问题。正常部署后端并重启 `everydayai-backend` 即可。异常时回滚本次代码并重新部署，不需要数据回滚。
