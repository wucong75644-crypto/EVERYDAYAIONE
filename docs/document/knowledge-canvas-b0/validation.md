# B0 验证记录

日期：2026-10-19。状态：B0 契约和本地原型检查通过；不代表业务功能已实现。

验证入口：

- `verify_contracts.py`：JSON Schema 正反例、内部引用、逐表约束引用、文档链接和 JavaScript 语法。
- `prototype.spec.cjs`：使用 Playwright 与本机 Chromium 操作独立原型，不启动应用服务。

## 结果

- 43 个 JSON Schema 通过结构检查；16 个正反例符合预期。
- 9 个拟新增 HTTP 路径、4 个模型只读工具的本地 schema 引用闭合；API 包装保持现有约定。
- 21 张拟新增表的主键、外键目标、唯一键、索引字段和 RLS 策略描述检查通过；已标注分批实施，其中 B1 为 9 张。
- 主方案和 B0 文档的本地链接、18 项需求编号、Markdown 围栏与原型 JavaScript 语法检查通过。
- Chromium 154.0.8037.57，1440×980，13 个交互场景通过；无页面 JavaScript 错误，无外部 HTTP 请求。
- 已人工查看[原型截图](./prototype.png)，检查移除聊天后的布局并补测折叠后搜索定位；最终截图与[浏览器结果](./prototype-results.json)一并保留。

浏览器场景覆盖：三选项卡与无导航创建按钮、范围搜索、定位和返回、双击创建、文档展开编辑、Tab/Enter/撤销、拉线到空白创建、拉线到已有节点关联、本地文本文件绑定与查看、画布不提供聊天及创建对话入口、折叠后的定位、子分支定时配置（校验来源、时间、接收目标）和根改名后身份稳定。

## 复现命令

在任务工作树根目录执行（使用环境已有依赖，未安装应用新依赖）：

```bash
/workspace/EVERYDAYAIONE/.venv/bin/python docs/document/knowledge-canvas-b0/verify_contracts.py
KB_PLAYWRIGHT_MODULE=/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright node docs/document/knowledge-canvas-b0/prototype.spec.cjs
```

浏览器环境禁止直接打开 file URL，因此验证脚本仅在 127.0.0.1 随机端口提供原型 HTML，完成后关闭静态服务器和浏览器。未启动应用后端或数据库。

## 实际边界

- JSON Schema 只能验证结构与反例，无法证明数据库 RLS、树防环、实时撤权或跨事务幂等正确；这些必须在 B1 及后续独立开发库中验证。
- 按用户最新决定已移除画布聊天框、对话节点创建、模拟回答、会话接口及会话链接表；仅保留既有主对话区 knowledge_refs 契约。主对话页面未在此原型中实现，真实 @ 候选、发送、模型导航和规则遵循仍须在 B4 集成验证。
- 定时任务仅保存原型本地状态，没有真实调度或企微发送；两处既有审计缺陷仍未修复。
- 本轮浏览器绑定验证使用文本；图片/视频本地播放器、Office 编辑、真实上传和媒体解析尚未集成验证。
- 未验证多人协作、完整 IME/移动端体验、生产迁移、CDN 鉴权和大画布性能。
- 原型刷新重置示例，不是正式数据持久化实现。未提交、推送或部署。
