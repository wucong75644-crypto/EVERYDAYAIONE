# Frontend Rules

本文件补充根 `AGENTS.md`，仅适用于 `frontend/`。

## React 与 TypeScript

- TypeScript 不新增 `any`；确有外部不可信输入时使用 `unknown` 并在边界解析。
- 避免不必要的全局状态和全局重渲染。
- WebSocket、定时器、订阅和事件监听必须 cleanup。
- 异步请求必须考虑卸载、过期响应和竞态，使用 AbortController、请求身份或项目现有等效机制。
- `setState` 在依赖旧状态时使用函数式更新。
- 复用现有组件、设计令牌和样式体系，不新增无范围约束的全局样式。

## 行为与安全

- UI 必须覆盖真实需要的 loading、empty、error、success 和权限状态。
- 不把请求失败伪装为空数据，不使用乐观 UI 替代服务端权威事实。
- 前端权限控制只改善体验，不能替代后端校验。
- 保持现有路由、状态和 API 合同；公共行为变化按 A级门禁确认。

## 验证

- 优先运行变更组件、Store、Hook 或服务的定向 Vitest。
- 根据影响运行 TypeScript、ESLint 和生产构建。
- 涉及异步状态时覆盖成功、失败、卸载、竞态和重试中实际相关的场景。
