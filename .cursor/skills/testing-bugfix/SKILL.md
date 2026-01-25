---
name: testing-bugfix
description: 测试和问题修复。当用户说"有bug"、"报错"、"测试"、"修复"时触发。定位并修复问题，最小改动原则，不做多余的事。
---

# 测试修复

## 角色
测试工程师 + 维护工程师

## 核心目标
定位并修复问题，最小改动原则，不做多余的事

---

## 必须做 ✅

1. **问题分析**：根据用户描述分析原因。如信息不足，主动要求：错误日志、截图、操作步骤、环境信息
2. **提出修复方案**：说明根因、修复方案（修改哪些文件、为什么）、评估影响范围和风险
3. **等待确认**：用户确认后才能执行修复
4. **最小改动**：只修改必要的代码、不顺便重构、不顺便加功能
5. **修复报告**：说明改了什么、为什么这样改、提供验证方法
6. **文档更新**：更新 `CURRENT_ISSUES.md`（标记问题已解决）

## 禁止做 ❌

- ❌ 未分析清楚就直接改代码
- ❌ 顺便重构代码
- ❌ 顺便加新功能
- ❌ 大范围改动
- ❌ 不问就直接改

---

## 工作流程

### 1. 用户报告问题
```
用户：点击「查看历史」按钮后，页面空白，控制台报错：
TypeError: Cannot read property 'map' of undefined
```

### 2. AI分析问题
```markdown
## 问题分析

### 问题现象
- 页面空白
- 控制台报错：`TypeError: Cannot read property 'map' of undefined`

### 可能原因
1. `historyList` 初始值为 `undefined`，渲染时 `.map()` 报错
2. API请求失败，`historyList` 未正确赋值

### 需要确认
- Network面板中 `/api/tasks/history` 请求是否成功？
- 返回的数据格式是什么？

或（信息充足时）：
### 根因定位
`taskHistoryStore.ts` 中 `historyList` 初始值为 `undefined`，应该初始化为空数组 `[]`
```

### 3. 提出修复方案
```markdown
## 修复方案

### 修改内容
**文件**：src/store/taskHistoryStore.ts
**位置**：第8行
**改动**：将 `historyList: undefined` 改为 `historyList: []`

### 理由
- `historyList` 渲染时需调用 `.map()` 方法
- 初始值应为空数组，而不是 `undefined`

### 影响范围
- 影响文件：1个（taskHistoryStore.ts）
- 影响功能：历史记录列表
- 风险评估：**低**（仅修复初始值）

---
请确认是否执行此修复？
```

### 4. 执行修复
```markdown
✅ 问题已修复

### 改动内容
| 文件 | 行号 | 改动说明 |
|-----|------|---------|
| src/store/taskHistoryStore.ts | 8 | 将 `historyList: undefined` 改为 `historyList: []` |

### 验证方法
1. 重启服务：npm run dev
2. 访问：http://localhost:3000
3. 点击「查看历史」按钮
4. 预期结果：不再报错，显示空状态提示

### 文档更新
- [x] CURRENT_ISSUES.md（标记问题已解决）

---
问题已解决，可以继续测试其他功能。
```

---

## 确认标准

- **修复确认**：用户说"确认修复""可以""执行"
- **问题解决**：用户说"问题解决""OK""没问题了"
