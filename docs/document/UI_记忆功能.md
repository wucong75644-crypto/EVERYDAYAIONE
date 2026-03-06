# UI 设计文档 — 记忆功能

## 1. 页面结构

### 1.1 记忆管理入口

**入口1：Sidebar 底部（主入口）**
- 位置：用户菜单上方
- 样式：`text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-2`
- 🧠图标 + "记忆管理"文字
- 点击 → 打开 MemoryModal

**入口2：ChatHeader 右侧（快捷入口）**
- 位置：积分显示左侧
- 样式：`text-gray-400 hover:text-gray-600`
- 🧠图标，hover tooltip "记忆管理"
- 点击 → 打开 MemoryModal
- Sidebar 折叠时也能访问

### 1.2 MemoryModal — 长期记忆 Tab（MVP）

- 弹窗容器：复用 `common/Modal.tsx`，`max-w-lg`
- 顶部：记忆功能全局开关 + 说明文案
- Tab 栏：[长期记忆] [每日记录]
- 统计 + 搜索栏
- 记忆条目列表（区域内滚动）
- 底部：手动添加 + 清空全部

### 1.3 MemoryModal — 每日记录 Tab（第二期）

- 按日期倒序分组
- 每条标注时间和来源（ERP查询/定时汇总/对话提取）
- 仅查看和删除，不可编辑
- 底部：保留天数选择 + 清空历史

### 1.4 对话中记忆反馈（MessageArea 内联）

- AI 提取记忆时：`· · · 🧠 已记住：[摘要] · · ·`
- 可点击展开完整内容 + [撤销记忆]
- 记忆功能关闭时不显示

## 2. 交互流程

1. 查看记忆：点击入口 → Modal → 骨架屏 → 列表
2. 编辑记忆：✏️ → textarea原地编辑 → 保存/取消
3. 删除记忆：🗑 → 红色高亮确认 → 滑出动画
4. 手动添加：+ → 顶部空条目 → 输入 → 保存
5. AI自动提取：对话后异步提取 → 内联提示
6. 用户"记住xxx"：AI回复确认 → 内联提示
7. 开关：toggle → toast提示 → 列表灰色
8. 清空：确认弹窗 → 全部删除
9. 搜索：前端实时过滤

## 3. 组件清单

| 组件 | 功能 | 复用/新建 | 所属期 |
|------|------|----------|-------|
| MemoryButton | Sidebar入口 | 新建 | MVP |
| MemoryModal | 管理弹窗主体 | 新建 | MVP |
| MemoryToggle | 全局开关 | 新建 | MVP |
| MemoryList | 列表容器 | 新建 | MVP |
| MemoryItem | 单条记忆 | 新建 | MVP |
| MemoryHint | 对话内联提示 | 新建 | MVP |
| MemorySearch | 搜索框 | 新建 | MVP |
| DailyMemoryList | 每日记录列表 | 新建 | 第二期 |
| DailyMemoryItem | 每日记录条目 | 新建 | 第二期 |
| RetentionSelector | 保留天数 | 新建 | 第二期 |

## 4. 设计规范

- 主色：blue-500/600（同现有）
- 条目：bg-gray-50 rounded-lg p-3 border border-gray-100
- 编辑态：border-blue-300 bg-blue-50/30
- 删除态：border-red-300 bg-red-50/30
- 动画：复用现有 animate-modalEnter/Exit，新增条目滑出 200ms
