# DEPRECATED: 意图学习模块（intent_learning + intent_distiller）

> **状态**：已删除
> **删除日期**：2026-04-11
> **影响范围**：1100 行代码 + 后台调度任务
> **追溯**：本文档作为未来重做时的设计参考，原始代码可在 git history `commit 32ac89b`（创建）和 `commit 9d34fab`（删除接入）中查看。

---

## 一、设计意图（自主进化闭环阶段 B + C）

设计文档：[TECH_路由提示词修复+自主进化闭环.md](TECH_路由提示词修复+自主进化闭环.md) §二

> 大脑不确定 → **给选项引导用户** → 记录选择结果 → 写入知识库 → 下次直接路由

```
用户："修正图片"
    ↓
大脑不确定 → ask_user（带选项引导）：
    "1. 编辑这张图片
     2. 基于这张图片生成新图片
     3. 用这张图片生成视频
     4. 只是想聊聊这张图片"
    ↓
用户选择：1
    ↓
写入 knowledge_nodes（category="experience", node_type="intent_pattern", source="user_confirmed"）
    ↓
下次任何用户说"修正图片" → 大脑从知识库读到此模式 → 直接路由
```

**模块构成**：
- `intent_learning.py`：阶段 B 写入路径（`record_ask_user_context` + `check_and_record_intent`）
- `intent_distiller.py`：阶段 C 提炼路径（`distill_intent_patterns`，定时任务用 LLM 把 intent_pattern 归纳成 distilled_rule）
- `background_task_worker._run_intent_distillation`：阶段 C 调度入口

---

## 二、为什么删除

### 直接原因：被重构遗忘

- **2026-03-12 commit `32ac89b`**：创建 `intent_learning.py` + `intent_distiller.py` + 测试文件，但模块本身**没有任何 caller**，需要后续接入到主流程
- **2026-03-12 ~ 2026-04-04**：旧 `agent_loop.py` + `agent_loop_infra.py` 接入了，详见 commit `9d34fab^:backend/services/agent_loop_infra.py:191-243`（已删）
- **2026-04-04 commit `9d34fab`**："删除 AgentLoop 全家桶 — 统一架构收官，净减 ~2400 行"
  - 删除 `agent_loop.py` + `agent_loop_infra.py` 共 17 文件 5159 行
  - **删除时把 `_record_ask_user_context` + `_check_intent_learning` 两个 helper 一并删了**，没有迁移到新的 `chat_handler.py`
  - **同时也删除了"路由层 ask_user 工具"** → 新架构 `chat_handler` 的 `_CORE_TOOLS` 不含 `ask_user`
- **2026-04-04 ~ 2026-04-11**：`intent_learning.py` 和 `intent_distiller.py` 成为孤儿模块；`intent_distiller` 仍在生产每个 poll cycle 空跑一次
- **2026-04-11**：本次清理彻底删除

### 根本原因：业界范式淘汰

| 范式 | 时代 | 业界状态（2026） |
|---|---|---|
| **ask_user 引导式意图学习**（Dialogflow / Rasa / LUIS 那一套） | 2018-2022 NLU 时代 | 已淘汰，只剩传统客服机器人/工单系统在用 |
| **从对话历史自动归纳用户偏好** | 2024+ | ChatGPT Memory / Claude Projects / Mem0 在做 |
| **Agent 自主反思学习**（Reflexion 论文） | 2024+ | 我们项目的 routing_pattern / failure_pattern 在做 |
| **Function Calling 智能路由** | 2024+ | 我们项目的 IntentRouter (千问) 在做 |

SOTA 产品扫描（ChatGPT / Claude / Cursor / Cline / Windsurf / Devin / Replit / Perplexity / Copilot）—— **没有一个**做"路由不确定时弹选项让用户选"的交互。它们的设计哲学统一是 **"零交互直接决策，错了用户改"**。

新架构（2026-04-04 单循环升级）整体哲学已经转向"零交互直接决策" —— 跟旧 intent_learning 设计依赖的"路由层主动 ask_user"是相反的方向。

---

## 三、当前替代机制

| 机制 | 干什么 | 文件 |
|---|---|---|
| **IntentRouter (千问 function calling)** | 用户消息 → 单次智能路由决策 | [intent_router.py](../../backend/services/intent_router.py) |
| **Mem0 用户记忆** | 通用对话历史 + 用户偏好 | [memory_service.py](../../backend/services/memory_service.py) + [memory_filter.py](../../backend/services/memory_filter.py) |
| **smart_model 路由** | 失败重试时切换模型 | [smart_model_config.py](../../backend/services/smart_model_config.py) |
| **ERPAgent 自学习经验** | 子 Agent retrospect 自己的工具调用 → 写 routing_pattern / failure_pattern | [erp_agent.py](../../backend/services/agent/erp_agent.py) `_record_agent_experience` |

这四件套共同覆盖了 intent_learning 的实际需求。

---

## 四、未来重做时的考虑

如果有一天要重新做"用户意图自动学习"，**不要简单复活旧代码**。先决定走哪个范式：

### 范式 A：LLM-as-Judge 事后归纳（推荐）
- **思路**：定时跑，取最近 N 条对话 → LLM 分析"哪些路由其实是错的" → 写规则
- **接入点**：可以复用 `background_task_worker` 的调度槽位（参考已删除的 `_run_intent_distillation`）
- **数据 schema**：可重新定义 `node_type='intent_pattern'`（命名空间已腾出）
- **依赖前提**：无（不需要交互）
- **业界先例**：OpenAI 的 RLHF 数据收集 / Anthropic 的 Constitutional AI

### 范式 B：反馈信号强化学习
- **思路**：用户重新生成 = 负反馈 / 用户继续追问 = 正反馈 → 训练分类器或 fine-tune
- **接入点**：要在 chat_handler 加用户反馈信号收集
- **依赖前提**：有大量带标签数据 + 训练基础设施
- **不适合独立项目**，更适合 SaaS 平台

### 范式 C：Agent 自主反思（已部分实现）
- **思路**：Agent retrospect 自己的工具调用 → 写经验 → 下次召回
- **接入点**：扩展 ERPAgent 的 `_record_agent_experience` 到 chat_handler 主循环
- **依赖前提**：无
- **当前进度**：ERPAgent 已实现 routing_pattern / failure_pattern，未推广到 chat_handler 主循环

### 范式 D：恢复 ask_user 引导式（不推荐）
- **思路**：跟旧 intent_learning 设计完全一样
- **依赖前提**：路由层要支持主动调 ask_user 给选项 + chat_handler 状态机能记录 user_response 和 confirmed_tool（这两个**当前架构都没有**，要重新设计）
- **业界已淘汰**，不推荐

---

## 五、清理清单（本次删除涉及的所有改动）

| 文件 | 操作 | 行数 |
|---|---|---|
| `backend/services/intent_learning.py` | 删除 | -128 |
| `backend/services/intent_distiller.py` | 删除 | -250 |
| `backend/tests/test_intent_learning.py` | 删除 | -323 |
| `backend/tests/test_intent_distiller.py` | 删除 | -410 |
| `backend/services/background_task_worker.py` | 删 `_run_intent_distillation` 调用 + 方法 + `_last_intent_distillation` 字段 | -22 |
| `backend/tests/test_model_scorer_integration.py` | 删 `TestRunIntentDistillation` 类（4 个测试） | -60 |
| `backend/services/knowledge_service.py` | 从 `_VALID_NODE_TYPES` 删 `intent_pattern` + `distilled_rule` | -3 |
| **合计** | | **约 -1196 行** |

---

## 六、追溯链路（git history）

```bash
# 看创建版本（2026-03-12，包含完整模块定义和接入逻辑）
git show 32ac89b -- backend/services/intent_learning.py
git show 32ac89b -- backend/services/intent_distiller.py

# 看 AgentLoop 接入版本（2026-04-04 删除前的最后一个版本）
git show 9d34fab^:backend/services/agent_loop_infra.py | sed -n '185,250p'
git show 9d34fab^:backend/services/agent_loop.py | grep -n "intent_learning\|_check_intent_learning\|_record_ask_user_context"

# 看 AgentLoop 删除 commit（2026-04-04，重构遗忘的根因）
git show 9d34fab --stat
```
