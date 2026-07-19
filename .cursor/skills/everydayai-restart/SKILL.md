---
name: "everydayai-restart"
description: "当用户说\"重启服务\"、\"重启一下\"、\"restart\"、\"杀掉进程\"、\"清理重启\"等需要重启开发环境时,执行完整的进程清理和服务重启"
---

# 重启开发服务

> **触发**：用户说"重启服务""重启一下""restart""杀掉进程"

## 执行流程

### 1. 杀死所有相关进程

```bash
# 杀死后端（uvicorn / python）
pkill -f "uvicorn" 2>/dev/null
pkill -f "python.*main.py" 2>/dev/null

# 杀死前端（vite / node）
pkill -f "vite" 2>/dev/null

# 强制释放端口（后端8000，前端3000）
lsof -ti:8000 | xargs kill -9 2>/dev/null
lsof -ti:3000 | xargs kill -9 2>/dev/null
```

### 2. 确认进程已清理

```bash
lsof -i:8000
lsof -i:3000
```

如果仍有残留，`kill -9` 强制清理，直到端口完全释放。

### 3. 重启服务

```bash
# 启动后端（端口8000，后台运行）
cd /Users/wucong/EVERYDAYAIONE && source backend/venv/bin/activate && python3 backend/main.py &

# 启动前端（端口3000，后台运行）
cd /Users/wucong/EVERYDAYAIONE/frontend && npm run dev &
```

### 4. 验证服务正常

等待几秒后检查：
- 后端：`curl -s http://localhost:8000/api/health` 或检查端口占用
- 前端：检查端口 3000 是否已监听

### 5. 输出报告

```
✅ 开发服务已完全重启

| 服务 | 端口 | 状态 |
|------|------|------|
| 后端 (FastAPI/Uvicorn) | 8000 | ✅ 运行中 |
| 前端 (Vite) | 3000 | ✅ 运行中 |

旧进程已全部清理，当前为全新进程。
```
