# 📚 项目文档中心

> **最后更新**：2026-01-23

---

## 📖 文档导航

### 核心设计文档

| 文档名称 | 描述 | 状态 |
|---------|------|------|
| [PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md) | 项目概述与核心功能说明 | ✅ 完成 |
| [TECH_ARCHITECTURE.md](./document/TECH_ARCHITECTURE.md) | 技术架构、数据库设计、API 设计 | ✅ 完成 |
| [PAGE_DESIGN.md](./document/PAGE_DESIGN.md) | 页面设计、交互流程、UI 规范 | ✅ 完成 |
| [OSS_CDN_DESIGN.md](./document/OSS_CDN_DESIGN.md) | OSS + CDN 存储方案设计 | ✅ 完成 |

### 开发辅助文档

| 文档名称 | 描述 | 状态 |
|---------|------|------|
| [FUNCTION_INDEX.md](./FUNCTION_INDEX.md) | 函数索引与代码结构 | ✅ 完成 |
| [CURRENT_ISSUES.md](./CURRENT_ISSUES.md) | 当前问题追踪与待办事项 | 🔄 持续更新 |
| [API_REFERENCE.md](./API_REFERENCE.md) | AI 模型 API 参考文档 | ✅ 完成 |

---

## 🎯 快速查找

### 我想了解...

- **项目是什么？做什么的？** → [PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md)
- **技术栈、架构设计、数据库？** → [TECH_ARCHITECTURE.md](./document/TECH_ARCHITECTURE.md)
- **页面长什么样？交互怎么做？** → [PAGE_DESIGN.md](./document/PAGE_DESIGN.md)
- **OSS 存储、CDN 加速、文件上传？** → [OSS_CDN_DESIGN.md](./document/OSS_CDN_DESIGN.md)
- **AI 模型 API、定价、使用示例？** → [API_REFERENCE.md](./API_REFERENCE.md)
- **某个函数在哪里？** → [FUNCTION_INDEX.md](./FUNCTION_INDEX.md)
- **当前有什么问题要解决？** → [CURRENT_ISSUES.md](./CURRENT_ISSUES.md)

### 开发注意事项

- **编码规范** → [../.cursorrules](../.cursorrules)
- **易踩坑规则** → [TECH_ARCHITECTURE.md - 第十章](./document/TECH_ARCHITECTURE.md#十开发注意事项易踩坑规则)
- **性能优化** → [PAGE_DESIGN.md - 3.2 多任务并发架构](./document/PAGE_DESIGN.md#32-多任务并发架构)

---

## 📁 文档结构

```
docs/
├── README.md                 # 本文件：文档导航
├── PROJECT_OVERVIEW.md       # 项目概述
├── FUNCTION_INDEX.md         # 函数索引
├── CURRENT_ISSUES.md         # 问题追踪
├── API_REFERENCE.md          # AI 模型 API 参考
├── database/                 # 数据库相关
│   └── DATABASE_GUIDE.md     # 数据库使用指南
└── document/                 # 核心设计文档
    ├── TECH_ARCHITECTURE.md  # 技术架构
    ├── PAGE_DESIGN.md        # 页面设计
    ├── OSS_CDN_DESIGN.md     # OSS + CDN 存储方案
    └── SUPER_ADMIN_FEATURES.md # 超管功能设计
```

---

## 🤖 AI 开发工具配置

### 规则文件

| 文件 | 适用工具 | 说明 |
|------|---------|------|
| `CLAUDE.md` | Claude Code | 项目根目录，自动加载基础规则 |
| `.cursorrules` | Cursor | 项目根目录，自动加载基础规则（含阶段工作流） |

### Claude Code 专用 Skills

> **位置**：`~/.cursor/skills/`
> **用途**：按需触发阶段工作流，实现职责分离

| Skill 文件 | 阶段 | 触发词 | 角色 |
|-----------|------|--------|------|
| `everydayai-requirement.md` | 1. 需求挖掘 | "我想要..." "实现一个..." "能不能加..." | 产品需求分析师 |
| `everydayai-ui-design.md` | 2. UI设计 | "界面设计" "页面布局" "交互流程" | UI/UX设计师 |
| `everydayai-tech-design.md` | 3. 技术设计 | "技术方案" "数据库设计" "API设计" | 系统架构师 |
| `everydayai-implementation.md` | 4. 开发执行 | "开始开发" "写代码" "实现功能" | 执行开发者 |
| `everydayai-testing.md` | 5. 测试修复 | "有bug" "报错" "测试" "修复" | 测试工程师 |

### Cursor 专用 Skills

> **位置**：`.cursor/skills/`（项目级，会随仓库共享）
> **用途**：AI 根据对话内容自动识别并触发，适用于 Cursor IDE

| Skill 目录 | 阶段 | AI 自动识别关键词 | 角色 |
|-----------|------|-----------------|------|
| `requirement-analysis/` | 1. 需求分析 | "我想要..." "实现一个..." "能不能加..." | 产品需求分析师 |
| `ui-design/` | 2. UI设计 | "界面设计" "页面布局" "交互流程" | UI/UX设计师 |
| `dev-doc/` | 3. 技术方案 | "技术方案" "数据库设计" "API设计" | 系统架构师 |
| `implementation/` | 4. 开发执行 | "开始开发" "写代码" "实现功能" | 执行开发者 |
| `testing-bugfix/` | 5. 测试修复 | "有bug" "报错" "测试" "修复" | 测试工程师 |

**特点**：
- AI 自动识别触发，无需手动 @ 或 /
- Skills 内容基于 `.cursor/rules/` 转换而来
- 规则优先级：`.cursorrules`（底层规则）> Cursor Skills

### 工作流程

```
新功能开发流程：
需求挖掘 → UI设计 → 技术设计 → 开发执行 → 测试修复

Bug修复/小改动：
直接进入测试修复阶段（简化流程）
```

### 注意事项

**Claude Code**：
- 基础规则由 `CLAUDE.md` 自动加载，无需手动触发
- Skills 存放在 `~/.cursor/skills/`，需手动触发或在特定场景下自动应用
- 所有阶段都必须遵守基础规则中的质量底线（500/120/15/4）

**Cursor**：
- 底层规则由 `.cursorrules` 自动加载，适用所有阶段
- Skills 存放在 `.cursor/skills/`，AI 根据对话内容自动识别触发
- 规则优先级：`.cursorrules` > Cursor Skills
- 所有阶段都必须遵守底层规则中的质量底线（500/120/15/4）

---

## ✅ 文档维护规则

根据 [../.cursorrules](../.cursorrules) 的要求：

- **新增/修改函数**：必须同时更新 `FUNCTION_INDEX.md`
- **修复问题**：必须同时更新 `CURRENT_ISSUES.md`
- **架构变更**：必须同时更新 `document/TECH_ARCHITECTURE.md`
- **UI 变更**：必须同时更新 `document/PAGE_DESIGN.md`
- **AI 模型 API 变更**：必须同时更新 `API_REFERENCE.md`

---

## 📌 重要提示

1. 所有文档使用 Markdown 格式
2. 代码示例必须标注语言（```python 或 ```javascript）
3. 重大变更必须更新文档的"最后更新"日期
4. 文档状态标识：✅ 完成 | 🔄 持续更新 | ⏳ 待完成

---

**维护者**：技术团队
