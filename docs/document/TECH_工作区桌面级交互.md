# 技术设计：工作区桌面级交互

## 1. 项目上下文

- **架构现状**：工作区 906 行前端代码（8 组件 + 1 hook），支持文件 CRUD、列表/网格、拖拽上传、内联重命名。后端 API 全部就绪。
- **可复用模块**：useWorkspace.move()、Dropdown（Radix）、WorkspaceDropZone、Modal
- **设计约束**：`--s-*` CSS 变量、lucide-react 图标、framer-motion 动画、组件 ≤500 行
- **潜在冲突**：无

## 2. 文件结构

### 新增
| 文件 | 职责 | 预估行数 |
|------|------|---------|
| hooks/useFileSelection.ts | 多选状态管理 | ~100 |
| workspace/FileContextMenu.tsx | 右键上下文菜单 | ~80 |
| workspace/BatchActionBar.tsx | 批量操作工具栏 | ~40 |

### 修改
| 文件 | 改动 |
|------|------|
| WorkspaceFileItem.tsx | +选中态+拖拽+右键、-三点菜单 |
| WorkspaceFileList.tsx | +列头排序+全选勾选框 |
| WorkspaceFileGrid.tsx | +图片缩略图 |
| WorkspaceView.tsx | +键盘事件+选择hook+批量操作+ContextMenu |
| useWorkspace.ts | +sortBy/sortOrder+batchRemove |

## 3. 开发任务

### Phase 1：选中 + 右键菜单
- 1.1 安装 @radix-ui/react-context-menu
- 1.2 新建 useFileSelection.ts
- 1.3 新建 FileContextMenu.tsx
- 1.4 改造 WorkspaceFileItem.tsx

### Phase 2：键盘 + 排序
- 2.1 WorkspaceView 键盘监听
- 2.2 useWorkspace 排序状态
- 2.3 WorkspaceFileList 列头排序

### Phase 3：拖拽移动 + 批量操作
- 3.1 WorkspaceFileItem 拖拽
- 3.2 新建 BatchActionBar.tsx
- 3.3 useWorkspace batchRemove
- 3.4 WorkspaceView 集成

### Phase 4：缩略图 + 收尾
- 4.1 图片缩略图
- 4.2 测试 + 文档

## 4. 依赖
- @radix-ui/react-context-menu ^1.0.2
