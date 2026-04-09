---
name: WS架构改动教训
description: 不要替换已验证的投递架构，修改前必须完整理解现有链路
type: feedback
---

修复 WS 消息投递问题时，不要用新架构（Redis Streams）替换已验证的旧架构（Pub/Sub），而是先排查真正的根因。

**Why:** 2026-04 尝试用 Redis Streams 替换 Pub/Sub，砍掉了 user 维度广播兜底，结果因为 WS 重连本身就有 bug（1006 误判认证失败 + 同 tab 登录不触发 connect），新架构依赖的 subscribe 链路压根不通，反而导致所有消息都不渲染。回滚后修复 WS 重连 bug 才真正解决问题。

**How to apply:** 遇到"消息不渲染"类问题，先用服务器日志确认：(1) WS 连接是否存在 (2) subscribe 是否到达后端 (3) 消息是否投递。从日志定位断裂点，而不是重新设计投递架构。
