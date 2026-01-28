# Claude Code 技能与代理指南

> 本文档详细说明 Claude Code 中可用的 Agents（代理）和 Skills（技能）的用途和使用方法。

---

## 一、Agents（代理）

代理是**自主执行任务的子进程**，拥有独立的工具集和上下文，适合处理独立的大型任务。

### 1. architect - 架构设计

**用途**：系统架构设计、技术方案评估

**使用场景**：
- 规划新功能的整体架构
- 选择技术栈和设计模式
- 评估系统可扩展性
- 做出架构层面的决策

**示例**：
```
"帮我设计一个实时通知系统的架构"
"评估使用 Redis vs PostgreSQL 做缓存的优劣"
```

---

### 2. planner - 任务规划

**用途**：复杂任务拆解、实现步骤设计

**使用场景**：
- 大型功能的实现规划
- 将复杂需求拆分为可执行步骤
- 识别实现风险和依赖
- 评估不同实现方案

**示例**：
```
"规划用户认证模块的实现步骤"
"帮我拆解这个重构任务"
```

---

### 3. code-reviewer - 代码审查

**用途**：代码质量检查、最佳实践验证

**使用场景**：
- 新代码写完后立即审查
- 检查潜在 bug 和代码异味
- 验证是否符合编码规范
- 提出改进建议

**自动触发**：写完代码后应主动使用

**检查项**：
- 代码可读性和命名
- 错误处理是否完善
- 性能问题
- 安全隐患

---

### 4. security-reviewer - 安全审查

**用途**：安全漏洞检测、OWASP Top 10 检查

**使用场景**：
- 处理用户输入的代码
- 认证/授权相关代码
- API 端点开发
- 敏感数据处理

**检查项**：
- SQL 注入
- XSS 跨站脚本
- CSRF 攻击
- 密钥硬编码
- 不安全的加密

---

### 5. tdd-guide - TDD 指导

**用途**：强制测试驱动开发流程

**使用场景**：
- 开发新功能
- 修复 bug
- 重构代码

**工作流程**：
```
1. RED   - 先写测试，测试失败
2. GREEN - 写最小实现，测试通过
3. REFACTOR - 优化代码，保持测试通过
4. 验证覆盖率 ≥80%
```

---

### 6. refactor-cleaner - 重构清理

**用途**：死代码清理、代码整合

**使用场景**：
- 删除未使用的代码
- 合并重复逻辑
- 代码库瘦身

**工具**：
- `knip` - 检测死代码
- `depcheck` - 检测未使用依赖
- `ts-prune` - TypeScript 未使用导出

---

### 7. doc-updater - 文档更新

**用途**：自动更新项目文档

**更新内容**：
- `docs/CODEMAPS/*` - 代码地图
- `README.md` - 项目说明
- `FUNCTION_INDEX.md` - 函数索引
- API 文档

**触发时机**：
- 新增/修改函数后
- 完成功能模块后
- 结构变更后

---

### 8. e2e-runner - 端到端测试

**用途**：Playwright E2E 测试执行

**功能**：
- 生成测试用例
- 运行 E2E 测试
- 截图/视频/trace 上传
- 管理测试旅程
- 隔离不稳定测试

**示例**：
```
"为登录流程创建 E2E 测试"
"运行所有 E2E 测试"
```

---

### 9. build-error-resolver - 构建错误修复

**用途**：快速修复构建/类型错误

**特点**：
- 最小化修改
- 只修复错误，不改架构
- 快速让构建通过

**使用场景**：
- TypeScript 类型错误
- 构建失败
- Lint 错误

---

## 二、Skills（技能）

技能是**领域知识和最佳实践的集合**，在当前会话中执行，通过 `/skill-name` 调用。

### 1. /frontend-patterns - 前端开发模式

**用途**：React/Next.js 前端最佳实践

**内容**：
| 领域 | 实践 |
|------|------|
| 组件设计 | 复合组件、受控/非受控模式 |
| 状态管理 | Context vs Zustand vs Redux 选择 |
| 性能优化 | memo、useMemo、useCallback |
| 样式方案 | CSS Modules、Tailwind |
| 数据获取 | SWR、React Query 模式 |

**使用**：
```
/frontend-patterns
"帮我优化这个组件的渲染性能"
```

---

### 2. /backend-patterns - 后端开发模式

**用途**：Node.js/Express/Next.js API 最佳实践

**内容**：
| 领域 | 实践 |
|------|------|
| API 设计 | RESTful、错误处理、版本控制 |
| 数据库 | 连接池、查询优化、事务 |
| 中间件 | 认证、日志、限流 |
| 架构 | Repository 模式、服务层 |
| 缓存 | Redis、内存缓存策略 |

**使用**：
```
/backend-patterns
"设计一个 API 端点处理用户注册"
```

---

### 3. /security-review - 安全审查

**用途**：代码安全检查清单

**检查项**：
```
□ 输入验证 - 防止 SQL 注入、XSS
□ 认证授权 - JWT/Session 安全配置
□ 敏感数据 - 密钥不硬编码，加密存储
□ API 安全 - Rate Limiting、CORS 配置
□ 依赖安全 - 无已知漏洞的依赖
□ 错误处理 - 不泄露敏感信息
```

**使用**：
```
/security-review
"检查这个登录 API 的安全性"
```

---

### 4. /tdd-workflow - TDD 工作流

**用途**：强制测试驱动开发

**流程**：
```
Step 1: 写用户故事
        As a [角色], I want to [动作], so that [收益]

Step 2: 生成测试用例
        describe('功能', () => {
          it('正常场景', ...)
          it('边界情况', ...)
          it('错误处理', ...)
        })

Step 3: 运行测试 → 应该失败 (RED)

Step 4: 实现代码 → 最小实现

Step 5: 运行测试 → 应该通过 (GREEN)

Step 6: 重构 → 优化代码

Step 7: 验证覆盖率 ≥80%
```

**使用**：
```
/tdd-workflow
"为搜索功能实现 TDD"
```

---

### 5. /continuous-learning - 持续学习

**用途**：从会话中提取可复用模式

**功能**：
- 识别重复出现的代码模式
- 将模式保存为新技能
- 积累项目特定知识
- 自动改进工作流

**使用**：
```
/continuous-learning
"总结这次会话中的模式"
```

---

### 6. /plan - 任务规划

**用途**：复杂任务的实现规划

**流程**：
1. 重述需求，确认理解
2. 评估风险和依赖
3. 创建分步实现计划
4. **等待用户确认后再写代码**

**使用**：
```
/plan
"实现一个实时聊天功能"
```

---

## 三、Agent vs Skill 对比

| 维度 | Agent（代理） | Skill（技能） |
|------|---------------|---------------|
| **本质** | 独立子进程 | 知识/指令集 |
| **执行** | 后台自主运行 | 当前会话执行 |
| **上下文** | 独立上下文 | 共享当前上下文 |
| **工具** | 受限工具集 | 全部工具 |
| **调用** | 自动/Task tool | `/skill-name` |
| **适用** | 独立大任务 | 指导当前任务 |

**简单理解**：
- **Agent** = 派出去独立干活的"员工"
- **Skill** = 我掌握的"知识技能"

---

## 四、使用建议

### 何时用 Agent
- 任务可以独立完成
- 需要深度分析（架构、安全）
- 任务较大，需要专注处理

### 何时用 Skill
- 需要指导当前开发
- 应用特定领域的最佳实践
- 需要共享当前会话上下文

### 最佳实践组合

| 任务类型 | 推荐组合 |
|----------|----------|
| 新功能开发 | `/plan` → `tdd-guide` → `code-reviewer` |
| Bug 修复 | `tdd-guide` → `code-reviewer` |
| 重构 | `refactor-cleaner` → `code-reviewer` |
| API 开发 | `/backend-patterns` → `security-reviewer` |
| 前端组件 | `/frontend-patterns` → `code-reviewer` |
| 安全敏感 | `security-reviewer` → `code-reviewer` |

---

## 五、快速参考

### Agents 一览
```
architect          - 架构设计
planner            - 任务规划
code-reviewer      - 代码审查
security-reviewer  - 安全审查
tdd-guide          - TDD 指导
refactor-cleaner   - 重构清理
doc-updater        - 文档更新
e2e-runner         - E2E 测试
build-error-resolver - 构建修复
```

### Skills 一览
```
/frontend-patterns   - 前端模式
/backend-patterns    - 后端模式
/security-review     - 安全审查
/tdd-workflow        - TDD 工作流
/continuous-learning - 持续学习
/plan                - 任务规划
```

---

*更新日期：2026-01-28*
