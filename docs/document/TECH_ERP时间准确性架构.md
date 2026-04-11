# TECH — ERP 时间准确性架构

> **任务等级**：A 级（涉及 22+ 文件、ERP 业务正确性核心）
> **状态**：方案 + 最终扫描已完成，待用户确认进 PR1（V2.2 法律 2.3 节）
> **依据**：审计报告 + 4 路并行调研（代码/测试/行业/工程）+ 4 路最终扫描（S1-S14）
> **创建日期**：2026-04-10
> **最后更新**：2026-04-10（吸收最终扫描结果）

---

## 目录

1. [背景与问题陈述](#1-背景与问题陈述)
2. [设计目标与非目标](#2-设计目标与非目标)
3. [多角度风险分析](#3-多角度风险分析)
4. [核心架构](#4-核心架构)
5. [数据契约](#5-数据契约)
6. [模块改动清单](#6-模块改动清单)
7. [边界场景穷举](#7-边界场景穷举)
8. [测试策略](#8-测试策略)
9. [迁移与灰度](#9-迁移与灰度)
10. [文档更新](#10-文档更新)
11. [回滚方案](#11-回滚方案)
12. [不在范围内的事](#12-不在范围内的事)
13. [决策记录（已锁定）](#13-决策记录已锁定)
14. [L4/L5 与现有安全层的耦合](#14-l4l5-与现有安全层的耦合)
15. [方案锁定后的最终扫描清单](#15-方案锁定后的最终扫描清单)
16. [执行顺序（PR 划分）](#16-执行顺序pr-划分)
17. [最终扫描结果与方案修订](#17-最终扫描结果与方案修订-2026-04-10-完成)

---

## 1. 背景与问题陈述

### 1.1 触发事件

2026-04-10 13:05，用户在企微小蓝中查询「4月10日 vs 同期对比」，模型回复中将 4 月 3 日错误标注为「上周四」。**实际上 2026-04-03 是周五**，4-10 也是周五，二者同周（ISO Week 14、Week 15）。

### 1.2 表象 vs 根因

| 层 | 表象 | 实际 |
|---|---|---|
| 数据层 | 数据正确（1769/2955）| 工具返回的数据与日期是对的 |
| 计算层 | 同比日期对齐对 | `_calc_period` 用 `datetime.now(CN_TZ)` |
| **语言层** | **「上周四」是幻觉** | **模型自己推算 weekday，无符号支撑** |

### 1.3 这不是孤例 — 是结构性问题

审计揭示了同类风险点远不止这一个：

| 风险点 | 文件 | 严重度 |
|---|---|---|
| ERP Agent 注入时间用 `_time.localtime()`（无时区）| [erp_agent.py:96](backend/services/agent/erp_agent.py#L96) | 🔴 P0 |
| 主聊天 Agent 也有同样的无时区注入 | [chat_context_mixin.py:116](backend/services/handlers/chat_context_mixin.py#L116) | 🔴 P0 |
| 同比/环比逻辑全部由 LLM 临场组合，无后端工具 | grep `同比\|环比\|compare` 全库 0 命中 | 🔴 P0 |
| ERP 数据表用 `TIMESTAMP`（无时区）而非 `TIMESTAMPTZ` | erp_document_items 等 | 🟡 P2 |
| `erp_sync_reconcile.py` 用 `datetime.now()` 算昨天范围，可能丢数据 | [erp_sync_reconcile.py:38](backend/services/kuaimai/erp_sync_reconcile.py#L38) | 🟡 P1 |
| `erp_sync_scheduler.py` 用 `datetime.now().hour == 3` 触发对账 | [erp_sync_scheduler.py:217](backend/services/kuaimai/erp_sync_scheduler.py#L217) | 🟡 P1 |
| `formatters/common.py:114` 默认时间范围用 naive datetime | [common.py:114](backend/services/kuaimai/formatters/common.py#L114) | 🟡 P1 |
| 进程未显式设置 TZ，依赖容器/OS 默认 | grep `TZ=\|tzset` 全库 0 命中 | 🔴 P0 |
| `CN_TZ = timezone(timedelta(hours=8))` 是工程坏味道 | erp_local_helpers.py:14 | 🟢 P2 |
| 测试覆盖几乎为零（5 个时间相关测试，<2%）| backend/tests/ | 🟡 P1 |
| 多租户表无 timezone 字段 | organizations 表 | 🟢 P3 |

### 1.4 学术与行业的共同结论

- **Date Fragments**（arxiv 2505.16088）：BPE 切分破坏日期语义，"不常见日期" 准确率最多掉 10 分
- **Temporal Blindness**（arxiv 2510.23853）：单点 prompt 注入不够，必须在工具返回里**冗余**事实
- **Looker / Quick BI / Tableau Pulse**：成熟 BI Copilot 一律把"相对时间解析"放在**平台侧**，让 LLM 只挑 enum
- **Anthropic / OpenAI / Google 官方文档**：function calling 的 datetime 字段一律用 `string + ISO 8601 描述`，没有一家推荐让模型自己算 weekday

### 1.5 中文场景的特殊性

- 「上周四」**默认指 ISO Week 中上一周的周四**，不是「7 天前那天」（与英文 "last Thursday" 语义不同）
- 「同比」=去年同月同日，「环比」=上一周期同位置 — 这两个语义必须由后端定义而非 LLM 解释
- 春节是电商年度最强的季节性事件，**春节对齐**的同比比纯日期对齐更有商业意义
- 大促周期（618/双11/双12）是准财年节点

---

## 2. 设计目标与非目标

### 2.1 目标

| # | 目标 | 验证标准 |
|---|---|---|
| G1 | **业务正确性**：ERP 时间相关回答 100% 准确，0 weekday 幻觉 | 100 例随机回归测试无错误 |
| G2 | **单一时钟**：全后端只有一个 SSOT 时间源 | grep `datetime.now()\|time.localtime()` 业务代码 0 残留 |
| G3 | **进程时区强制**：容器/进程级显式 `Asia/Shanghai`，不依赖 OS 默认 | 启动时 sanity check 通过 |
| G4 | **结构化时间事实**：所有时间相关工具返回带 `weekday_cn / iso_week / relative_label` 三元组 | schema 校验强制 |
| G5 | **双重冗余注入**：system prompt 注入 + 工具返回内嵌，禁止模型自己算 weekday | 提示词 + 工具改造完毕 |
| G6 | **同比/环比工具化**：新增 `local_compare_stats`，对比逻辑由后端计算 | LLM 不再调 `local_global_stats` 两次做对比 |
| G7 | **节假日感知**：识别周末/法定假日/调休/春节窗口/大促窗口 | 集成 chinese-calendar |
| G8 | **测试可冻结**：引入 time-machine，所有时间相关代码可单测 | freeze 时间 fixture 可用 |
| G9 | **可观测性**：偏离日志记录所有"模型仍然出错"的场景 | 上线后看板可见 |

### 2.2 非目标（本次不做）

- ❌ **多租户多时区支持**（org.timezone 字段）— 留作 P3 后续，目前所有 org 都在中国
- ❌ **ERP 数据表 TIMESTAMP → TIMESTAMPTZ 迁移** — 数据迁移风险高，留作独立任务
- ❌ **农历转换 / 春节自动对齐为默认** — 仅做"春节窗口标记"，公历对齐为默认
- ❌ **国际化（英文 weekday/月份）** — 当前产品仅服务中国用户
- ❌ **历史时区规则**（中国 1986-1991 夏令时、1949 前 LMT）— 业务数据均在 2020+
- ❌ **erp_sync_*.py 内部所有 naive datetime 修复** — 仅修 reconcile + scheduler，其余作为后续工程清理（加 TODO 标记）

### 2.3 范围调整说明（2026-04-10 锁定）

L4 输出层校验和 L5 偏离日志**纳入本次范围**，作为 [TECH_Agent架构安全层补全.md](TECH_Agent架构安全层补全.md) 的 Phase 7 实现。详见 §14。

之所以纳入：复用现有安全层架构（Phase 1-6）的装饰器位置和审计日志基础设施，边际成本低。

---

## 3. 多角度风险分析

### 3.1 业务风险

| 风险 | 场景 | 后果 | 当前是否暴露 |
|---|---|---|---|
| weekday 幻觉 | 任何同比/环比对比 | 用户基于错误"上周四"做经营决策 | ✅ 已暴露 |
| 跨午夜漂移 | 23:55 提问"今天订单"，工具 0:01 返回 | 数据是昨天的，回复说"今天" | ⚠️ 潜在 |
| 容器时区漂移 | 生产服务器没设 TZ | 16:00 后所有"今天"查到的是昨天 | ⚠️ 定时炸弹 |
| 双时钟冲突 | prompt 注入 vs 工具内部 | system 说"今天 4-10"，工具查 "4-10 北京时间"，可能错 1 天 | ⚠️ 潜在 |
| 同比基准漂移 | 春节同比时去年正月初一 vs 今年大年三十 | 同比数据完全无意义 | ⚠️ 潜在 |
| 调休误判 | "上个工作日" 在调休补班日 | 算错"上个工作日" | ⚠️ 潜在 |
| 对账丢数据 | sync_reconcile 算"昨天范围"用 naive datetime + 容器 UTC | 凌晨对账查 UTC 昨天 = 北京时间 8:00-32:00，丢数据 | ⚠️ 潜在 |

### 3.2 工程风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `os.environ['TZ']` + `tzset()` 在 uvicorn worker 间不一致 | 多 worker 时间漂移 | 改用 Dockerfile ENV |
| `python:3.11-slim` 默认无 tzdata，`ZoneInfo("Asia/Shanghai")` 抛异常 | 启动失败 | Dockerfile 装 tzdata + 启动 sanity check |
| `locale.setlocale` 全局非线程安全 | 偶发崩溃 | 不用 locale，用映射字典 |
| Pydantic v2 裸 ISO 字符串解析为 naive | 隐式去时区 | 用 `AwareDatetime` 类型 |
| chinese-calendar 库每年 11-12 月才更新次年假期 | 跨年时空窗 | pin 版本 + CI 校验 + 手动 12 月更新提醒 |
| freezegun 与 asyncio 不兼容 | 测试不稳定 | 改用 time-machine |
| 工具返回 schema 变更 | 现有测试断言失败 | 灰度 + 双发字段（保留旧字段一段时间）|
| 注入额外 prompt 增加 token | 每次请求 +50 token | 可接受（<1% 增长）|

### 3.3 合规与可观测性

| 关注点 | 现状 | 改造后 |
|---|---|---|
| 审计日志时间字段 | TIMESTAMPTZ ✓ | 不变 |
| 数据查询时间范围可追溯 | 仅日志可见 | 工具返回 `start_iso` + `end_iso` 显式记录 |
| 事实偏离监控 | 无 | L5 复用 Phase 6 审计日志（详见 §14） |

### 3.4 扩展性

| 维度 | 当前 | 改造后 |
|---|---|---|
| 单时区→多时区 | 写死 Asia/Shanghai | RequestContext 持有 tz，未来可从 org.timezone 取 |
| 同比→任意周期对比 | 无 | `local_compare_stats` 支持 7 种内置 enum + custom |
| 中国节日→国际节日 | 无 | chinese-calendar 抽象为 `is_workday()` 接口，未来可换库 |

---

## 4. 核心架构

### 4.1 神经-符号分离原则

> **铁律**：凡是有唯一正确答案的事实（日期/星期/相对时间/工作日），必须由代码计算；模型只负责语言表达。

### 4.2 三层防护

```
┌──────────────────────────────────────────────────────────┐
│  L3  ERP_ROUTING_PROMPT 硬规则                            │  ← 兜底
│      "禁止自行推算 weekday/相对时间"                       │
├──────────────────────────────────────────────────────────┤
│  L2  工具返回结构化时间字段                                │  ← 数据契约
│      TimePoint / DateRange / ComparePoint                │
├──────────────────────────────────────────────────────────┤
│  L1  RequestContext 单一时间源                            │  ← SSOT
│      启动时 sanity-check + 每请求构造 + 全链路传递        │
└──────────────────────────────────────────────────────────┘
                           ↑
              ┌────────────┴─────────────┐
              │  L0  进程级时区固化        │  ← 物理基础
              │  Dockerfile ENV TZ        │
              │  + tzdata 包              │
              │  + ZoneInfo("Asia/Shanghai") │
              └──────────────────────────┘
```

### 4.3 各层职责

- **L0 物理层**：保证进程读到的"now"在任何容器/服务器上都是中国时间
- **L1 应用层**：单一时间源 + 中央化工具函数，禁止业务代码裸调 `datetime.now()`
- **L2 数据层**：所有时间相关工具返回结构化字段，模型只复述不计算
- **L3 提示词层**：双重冗余 + 硬规则约束模型行为

### 4.4 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 时区库 | **`zoneinfo` 标准库 + `tzdata` pip 包** | Python 3.11 内置，无需 pytz |
| 时区设置位置 | **Dockerfile ENV TZ + Python sanity check** | 不污染代码，logging 自动对齐 |
| 时区表示 | **`ZoneInfo("Asia/Shanghai")`** 替代 `timezone(timedelta(hours=8))` | IANA 标准，可演进 |
| 中文星期 | **映射字典 `["周一",...,"周日"]`** | 不依赖 locale，线程安全 |
| 节假日库 | **chinese-calendar** | 含调休/补班，国务院通知更新及时 |
| 测试冻结 | **time-machine** | 比 freezegun 快 10x，asyncio 兼容 |
| RequestContext 注入位置 | **HTTP/WS handler 入口** | 一次构造，不可变，全链路传递 |
| 同比/环比 | **新增 `local_compare_stats` 工具** | 不让 LLM 调两次再口述对比 |
| 工具返回兼容性 | **新字段叠加，旧字段保留 1 个迭代** | 灰度安全 |

---

## 5. 数据契约

### 5.1 核心类型定义（`backend/utils/time_context.py` 新建）

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")

WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(frozen=True)
class TimePoint:
    """单个时间点的结构化表示。

    LLM 直接使用 weekday_cn / display_cn，不要自己算 weekday。
    """
    iso: str                    # "2026-04-10T13:05:00+08:00"
    date_str: str               # "2026-04-10"
    weekday: int                # 0=周一, 6=周日
    weekday_cn: str             # "周五"
    iso_week: int               # 15
    iso_year: int               # 2026
    is_workday: bool            # 节假日感知
    is_holiday: bool            # 法定假日
    holiday_name: Optional[str] # "清明节"
    is_lieu: bool               # 调休补班日
    is_spring_festival_window: bool  # 春节前后±15天
    display_cn: str             # "2026年4月10日 周五"
    relative_label: str         # "今天" / "昨天" / "前天" / "3天前" / "上周五"

    @classmethod
    def from_datetime(cls, dt: datetime) -> "TimePoint": ...

    @classmethod
    def from_date(cls, d: date) -> "TimePoint": ...


@dataclass(frozen=True)
class DateRange:
    """时间范围的结构化表示。"""
    start: TimePoint
    end: TimePoint
    period_kind: Literal[
        "day", "week", "month", "quarter", "year",
        "last_n_days", "custom",
    ]
    period_label: str           # "2026-04-10 周五" / "本周（4-06~4-12）"
    span_days: int              # 范围跨度（天）
    workday_count: int          # 范围内工作日数（节假日除外）

    @classmethod
    def for_today(cls, ctx: "RequestContext") -> "DateRange": ...

    @classmethod
    def for_yesterday(cls, ctx: "RequestContext") -> "DateRange": ...

    @classmethod
    def for_this_week(cls, ctx: "RequestContext") -> "DateRange":
        """ISO 周（周一到周日）。"""

    @classmethod
    def for_last_week(cls, ctx: "RequestContext") -> "DateRange": ...

    @classmethod
    def for_this_month(cls, ctx: "RequestContext") -> "DateRange": ...

    @classmethod
    def for_last_month(cls, ctx: "RequestContext") -> "DateRange": ...

    @classmethod
    def for_last_n_days(cls, ctx: "RequestContext", n: int) -> "DateRange": ...

    @classmethod
    def custom(cls, start_dt: datetime, end_dt: datetime) -> "DateRange": ...


@dataclass(frozen=True)
class ComparePoint:
    """同比/环比对比的结构化表示。"""
    current: DateRange
    baseline: DateRange
    compare_kind: Literal[
        "wow",        # 周环比 (Week over Week)
        "mom",        # 月环比 (Month over Month)
        "yoy",        # 年同比 (Year over Year)
        "spring_aligned",  # 春节对齐（去年春节同位置）
        "custom",
    ]
    compare_label: str          # "环比上周同期" / "同比去年同期"
    semantic_note: str          # "本系统上周指 ISO 周一至周日"


@dataclass(frozen=True)
class RequestContext:
    """每个请求生命周期内的不可变时间事实。

    在 HTTP/WS handler 入口构造一次，全链路传递，禁止下游重新计算 now。
    """
    now: datetime               # aware, ZoneInfo("Asia/Shanghai")
    today: TimePoint            # 即 now 对应的 TimePoint
    user_id: str
    org_id: str
    tz_name: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    request_id: str = ""

    @classmethod
    def build(cls, user_id: str, org_id: str, request_id: str = "") -> "RequestContext":
        """入口工厂方法。"""
        now = datetime.now(CN_TZ)
        return cls(
            now=now,
            today=TimePoint.from_datetime(now),
            user_id=user_id,
            org_id=org_id,
            request_id=request_id,
        )

    def for_prompt_injection(self) -> str:
        """生成 system prompt 时间注入字符串。"""
        return (
            f"当前时间：{self.now.strftime('%Y-%m-%d %H:%M')} "
            f"{self.today.weekday_cn}（中国时区 UTC+8，ISO 第 {self.today.iso_week} 周）"
        )
```

### 5.2 工具返回 schema 强约束

所有 ERP 数据查询工具的返回（无论是字符串还是结构化）**必须包含**：

- 顶部一行结构化时间块（人类可读且模型可复述）：
  ```
  [统计区间] 2026-04-10 周五（今天） 00:00–13:05（北京时间）
  ```
- 同比/环比的额外块：
  ```
  [对比基线] 2026-04-03 周五（上周同期，环比） 00:00–13:05
  [语义约定] "上周" = ISO 周一至周日的上一周
  ```

### 5.3 Function Calling Schema 改造

**`local_compare_stats`（新增工具）**：

```python
{
    "type": "function",
    "function": {
        "name": "local_compare_stats",
        "description": "时间维度对比统计（同比/环比/任意区间对比）。"
                       "禁止 LLM 自行调用 local_global_stats 两次拼对比 — 必须用本工具。"
                       "工具返回包含完整的对比时间事实（含中文星期）。",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string", "enum": [...]},
                "compare_kind": {
                    "type": "string",
                    "enum": ["wow", "mom", "yoy", "spring_aligned", "custom"],
                    "description": (
                        "wow=环比上周同期；mom=环比上月同期；"
                        "yoy=同比去年同期；spring_aligned=春节对齐同比；"
                        "custom=自定义两个区间"
                    ),
                },
                "current_period": {
                    "type": "string",
                    "enum": ["today", "yesterday", "this_week",
                             "this_month", "last_n_days", "custom"],
                },
                "current_n": {"type": "integer",
                              "description": "current_period=last_n_days 时使用"},
                "current_start": {"type": "string",
                                  "description": "ISO 8601, current_period=custom 时使用"},
                "current_end": {"type": "string"},
                "baseline_start": {"type": "string",
                                   "description": "compare_kind=custom 时使用"},
                "baseline_end": {"type": "string"},
                # 复用 local_global_stats 的过滤参数
                "shop_name": {"type": "string"},
                "platform": {"type": "string"},
                "time_type": {"type": "string", "enum": [...]},
                "rank_by": {"type": "string", "enum": [...]},
                "group_by": {"type": "string", "enum": [...]},
            },
            "required": ["doc_type", "compare_kind", "current_period"],
        },
    },
}
```

**`local_global_stats`（增强）**：保留所有现有参数，**只在返回字符串顶部加结构化时间块**。

---

## 6. 模块改动清单

### 6.1 Phase A：基础设施（必须先做）

| # | 文件 | 改动类型 | 内容 | 行数 |
|---|---|---|---|---|
| **A0** 🔴 | [`backend/services/kuaimai/client.py:181/192/271`](backend/services/kuaimai/client.py#L181) | 修改 | **快麦 API 签名时间戳** `datetime.now()` → `datetime.now(CN_TZ)`。**P0 紧急**：服务器 TZ 漂移会导致所有快麦 API 请求被签名校验拒绝 | -3/+3 |
| A1 | `backend/utils/__init__.py` | 新建 | 包初始化 | <5 |
| A2 | `backend/utils/time_context.py` | 新建 | `TimePoint` / `DateRange` / `ComparePoint` / `RequestContext` / `CN_TZ` / 工厂方法 | ~400 |
| A3 | `backend/utils/relative_label.py` | 新建 | 相对时间标签计算（"今天/昨天/上周X/N天前"） | ~80 |
| A4 | `backend/utils/holiday.py` | 新建 | chinese-calendar 薄封装 + 春节窗口/大促窗口判断 + 启动时年份覆盖检查 | ~80 |
| A5 | `backend/main.py` | 修改 | 启动时 `ZoneInfo("Asia/Shanghai")` sanity check + chinese-calendar 年份覆盖 warning | +20 |
| A6 | `backend/requirements.txt` | 修改 | +`tzdata`、`chinese-calendar==1.10.0`、`time-machine==2.14.1`（dev）| +3 |
| **A7** 🔴 | [`deploy/everydayai-backend.service:14`](deploy/everydayai-backend.service#L14) | 修改 | systemd unit 加 `Environment="TZ=Asia/Shanghai"` | +1 |
| **A7b** 🔴 | [`deploy/everydayai-wecom.service:13`](deploy/everydayai-wecom.service#L13) | 修改 | systemd unit 加 `Environment="TZ=Asia/Shanghai"` | +1 |
| A7c | `deploy/deploy.sh` / `deploy/setup-server.sh` | 修改 | 部署脚本里 `apt install tzdata` 兜底 | +2 |
| A8 | `backend/tests/conftest.py` | 修改 | 新增 `frozen_time` fixture（基于 time-machine） | +20 |

> **L0 部署方式确认**：远程 SSH + venv，**已找到** systemd unit 文件。L0 在 systemd unit 加环境变量即可，**不需要 Python 内部调 `tzset()`**（避免 uvicorn 多 worker 不一致）。

### 6.2 Phase B：核心改造（修业务正确性）

#### 6.2.1 Prompt 注入与基础 CN_TZ 替换

| # | 文件 | 改动类型 | 内容 | 行数 |
|---|---|---|---|---|
| B1 | [`backend/services/agent/erp_agent.py:96`](backend/services/agent/erp_agent.py#L96) | 修改 | 用 `RequestContext.for_prompt_injection()` 替换 `_time.localtime()` | -2/+5 |
| B2 | `backend/services/agent/erp_agent.py:__init__` | 修改 | 接收并保存 `RequestContext` 参数 | +3 |
| B3 | [`backend/services/handlers/chat_context_mixin.py:116`](backend/services/handlers/chat_context_mixin.py#L116) | 修改 | 同 B1 | -2/+5 |
| B4 | [`backend/services/kuaimai/erp_local_helpers.py:14`](backend/services/kuaimai/erp_local_helpers.py#L14) | 修改 | `CN_TZ = ZoneInfo("Asia/Shanghai")`（保留 import 兼容） | -1/+2 |
| B10 | [`backend/services/kuaimai/formatters/common.py:114`](backend/services/kuaimai/formatters/common.py#L114) | 修改 | `datetime.now()` → `datetime.now(CN_TZ)` | -1/+1 |

#### 6.2.2 工具返回结构化时间块（**8 个工具**）

> **来自最终扫描修订**：之前只识别了 1 个工具，实际有 8 个 ERP 工具会处理时间相关数据，必须全部加结构化时间块。

| # | 文件 | 工具名 | 时间块格式 |
|---|---|---|---|
| B5a | [`erp_local_global_stats.py`](backend/services/kuaimai/erp_local_global_stats.py) | `local_global_stats` | `[统计区间] YYYY-MM-DD 周X 00:00–HH:MM` |
| B5b | [`erp_local_query.py:24`](backend/services/kuaimai/erp_local_query.py#L24) | `local_purchase_query` | `[查询时间] YYYY-MM-DD 周X · 近N天` |
| B5c | [`erp_local_query.py:112`](backend/services/kuaimai/erp_local_query.py#L112) | `local_aftersale_query` | 同上 |
| B5d | [`erp_local_query.py:168`](backend/services/kuaimai/erp_local_query.py#L168) | `local_order_query` | 同上 |
| B5e | [`erp_local_query.py:239`](backend/services/kuaimai/erp_local_query.py#L239) | `local_product_flow` | 同上 |
| B5f | [`erp_stats_query.py:18`](backend/services/kuaimai/erp_stats_query.py#L18) | `local_product_stats` | 同 B5a |
| B5g | [`erp_local_doc_query.py:27`](backend/services/kuaimai/erp_local_doc_query.py#L27) | `local_doc_query` | 同 B5b |
| B5h | [`erp_local_db_export.py:181`](backend/services/kuaimai/erp_local_db_export.py#L181) | `local_db_export` | `[导出时间] YYYY-MM-DD HH:MM 周X · 数据截止 YYYY-MM-DD` |

每个工具改动 +5~15 行，统一通过 `time_context.format_time_header(ctx, kind, ...)` helper 实现，避免重复代码。

#### 6.2.3 同比/环比工具新增

| # | 文件 | 改动类型 | 内容 | 行数 |
|---|---|---|---|---|
| B6 | `backend/services/kuaimai/erp_local_compare_stats.py` | 新建 | 同比/环比工具实现 — DB 端 RPC 双查 + 结构化对比返回 | ~250 |
| B7 | `backend/migrations/057_erp_compare_stats_rpc.sql` | 新建 | DB 端 RPC `erp_compare_stats_query`（双查 + 单事务） | ~120 |
| B8 | [`backend/config/erp_local_tools.py`](backend/config/erp_local_tools.py) | 修改 | 新增 `local_compare_stats` 工具定义 | +50 |
| B9 | [`backend/config/erp_tools.py`](backend/config/erp_tools.py) | 修改 | `ERP_ROUTING_PROMPT` 加硬规则 + 新增 compare 路由 | +20 |

#### 6.2.4 RequestContext 注入入口（**复用 OrgCtx 模式**）

> **扫描发现**：项目已有 `OrgCtx` 依赖注入（`api/routes/message.py:131` 等），RequestContext 直接复用此模式，不重复造轮子。

| # | 文件 | 改动类型 | 内容 | 行数 |
|---|---|---|---|---|
| B11 | `backend/api/dependencies.py`（已存在的 OrgCtx 位置） | 修改 | 新增 `RequestCtx = Depends(...)` 工厂，内部包装 OrgCtx + 构造 RequestContext | +20 |
| B12 | `backend/api/routes/message.py` / `conversation.py` | 修改 | 把 `OrgCtx` 升级为 `RequestCtx` | -2/+2 each |
| B13 | [`backend/api/routes/wecom.py:62`](backend/api/routes/wecom.py#L62) | 修改 | `receive_message` 从 XML 提取 user_id 后构造 RequestContext | +10 |
| B14 | [`backend/api/routes/wecom.py:97`](backend/api/routes/wecom.py#L97) | 修改 | `_process_callback_xml` 异步处理时透传 RequestContext | +5 |
| B15 | `backend/api/routes/ws.py`（如有）| 修改 | WebSocket 入口注入 RequestContext | +10 |
| B16 | `backend/services/agent/erp_agent.py:__init__` | 修改 | 接收 `RequestContext` 参数 | +3 |

### 6.3 Phase C：定时任务修复

| # | 文件 | 改动类型 | 内容 |
|---|---|---|---|
| C1 | `backend/services/kuaimai/erp_sync_reconcile.py:38` | 修改 | `datetime.now()` → `datetime.now(CN_TZ)` |
| C2 | `backend/services/kuaimai/erp_sync_scheduler.py:217,239,247` | 修改 | 同上 + 用 `now_cn().hour` 比较 |

> **不在本次修复**：`erp_sync_worker.py` / `erp_sync_executor.py` / `erp_sync_dead_letter.py` / `erp_sync_config_handlers.py` 等内部 `datetime.now()` 共 30+ 处。这些是同步内部状态时间戳，不直接影响查询正确性，留作独立工程清理任务。**但**会在本次给所有这些文件加 `# TODO(time-context): 迁移到 now_cn()` 标记，方便后续批量迁移。

### 6.4 Phase D：测试

| # | 文件 | 改动类型 | 内容 |
|---|---|---|---|
| D1 | `backend/tests/test_time_context.py` | 新建 | TimePoint/DateRange/ComparePoint 单测 |
| D2 | `backend/tests/test_erp_compare_stats.py` | 新建 | local_compare_stats 工具测试 |
| D3 | `backend/tests/test_erp_local_v2.py` | 修改 | 加跨午夜/跨月/跨年/leap year 边界测试 |
| D4 | `backend/tests/test_erp_agent.py` | 修改 | 验证 system message 注入格式（time-machine freeze） |
| D5 | `backend/tests/test_holiday.py` | 新建 | 法定假日/调休/春节窗口判断 |

详见 [§8 测试策略](#8-测试策略)。

### 6.5 Phase E：文档

| # | 文件 | 改动 |
|---|---|---|
| E1 | `docs/document/PROJECT_OVERVIEW.md` | 添加 `backend/utils/time_context.py` 文件说明 |
| E2 | `docs/document/FUNCTION_INDEX.md` | 收录 `RequestContext` / `TimePoint` / `local_compare_stats` |
| E3 | `docs/document/TECH_ERP本地优先统一查询架构.md` | 引用本文档作为时间事实层规范 |
| E4 | `~/.claude/projects/-Users-wucong-EVERYDAYAIONE/memory/` | 新增 project memory：时间事实层架构决策 |

### 6.6 改动汇总（已根据最终扫描修订）

- **新建文件**：8 个（time_context / relative_label / holiday / erp_local_compare_stats / 迁移 SQL / 3 个测试）
- **修改文件**：~22 个（**含 8 个 ERP 工具时间块改造 + 企微入口 + systemd unit + 快麦 client 签名修复**）
- **代码行数**：约 +1100 / -25
- **依赖新增**：3 个（`tzdata`、`chinese-calendar==1.10.0`、`time-machine==2.14.1`）
- **TODO 标记**：约 30 处（sync 层 naive datetime，留作独立任务）

### 6.7 PR 拆分（最终版）

| PR | 范围 | 关键产物 | 风险 |
|---|---|---|---|
| **PR1** | A0(快麦签名 P0) + A1-A8 + B1-B16（除 L4/L5）| 业务正确性核心 + 8 工具时间块 + 同比工具 + 入口注入 | 中 |
| **PR2** | L4 TemporalValidator + L5 偏离日志（复用 Phase 6） | 安全层 Phase 7 | 低 |
| **PR3** | C1+C2 定时任务 + D1-D5 测试 + E1-E4 文档 + 全量 sync 层加 TODO | 测试覆盖 + 文档 | 低 |

---

## 7. 边界场景穷举

### 7.1 时间维度边界

| # | 场景 | 现状行为 | 改造后行为 | 测试 |
|---|---|---|---|---|
| 1 | 23:59 提问"今天" | 工具返回当天 23:59 数据 | RequestContext 在入口冻结 now，全链路用同一时刻 | D3-1 |
| 2 | 跨午夜请求（提问 23:59，工具 0:01 返回）| now 在工具内重新算 → 跨天 | RequestContext 的 now 不变，工具结果仍归"今天" | D3-2 |
| 3 | 周日 23:59 → 周一 0:01 | "本周"切换 | 同上，本周不变 | D3-3 |
| 4 | 月末 31 日 → 1 日 | "本月"切换 | 同上 | D3-4 |
| 5 | 12-31 → 01-01 跨年 | "今年"切换 | 同上 | D3-5 |
| 6 | 闰年 02-29 | `datetime.replace(year=year-1)` 抛 ValueError | yoy 对比时降级到 02-28 | D3-6 |
| 7 | 月末 31 日的"上月同日" | 31 → 31 不存在（如 4-31）| 降级到上月最后一天（4-30）| D3-7 |
| 8 | 春节同比 | 公历对齐 → 去年正月初一 vs 今年大年三十 | spring_aligned 模式：找去年春节起点对齐 | D5-1 |
| 9 | 节假日中提问"上个工作日" | 模型自己算 | chinese-calendar 算 | D5-2 |
| 10 | 调休补班日 | 模型不知道 | `is_lieu=True` 标记 | D5-3 |

### 7.2 时区维度边界

| # | 场景 | 现状 | 改造后 |
|---|---|---|---|
| 11 | 容器 TZ=UTC | `_time.localtime()` 给 UTC | 启动 sanity check 失败 fail-fast |
| 12 | 容器 TZ 未设置 | 同上 | 同上 |
| 13 | tzdata 未装 | `ZoneInfo("Asia/Shanghai")` 抛 ZoneInfoNotFoundError | 启动 sanity check 失败 fail-fast |
| 14 | NTP 时钟跳变（NTP sync 跳了 5 秒）| 影响 sleep / monotonic | 业务时间用 `datetime.now(CN_TZ)` 不受影响；超时计算用 `monotonic()` 不受影响 |
| 15 | 服务器在国外（如香港 AWS）| 取决于容器 TZ 设置 | Dockerfile ENV 强制 |
| 16 | 用户在国外用 VPN | 用户的设备时间不影响 | RequestContext 用服务器 now，与设备无关 |

### 7.3 数据维度边界

| # | 场景 | 现状 | 改造后 |
|---|---|---|---|
| 17 | TIMESTAMP 列读出来是 naive | DB 默认按服务器 TZ 解释 | 假定为 CN_TZ（写入侧也用 CN_TZ），用 `dt.replace(tzinfo=CN_TZ)` |
| 18 | TIMESTAMPTZ 列读出来 | aware UTC | 用 `dt.astimezone(CN_TZ)` 转换 |
| 19 | 历史数据迁移期间存在两种格式 | 混乱 | 不在本次范围，单独任务 |

### 7.4 LLM 行为维度边界

| # | 场景 | 现状 | 改造后 |
|---|---|---|---|
| 20 | 模型仍旧自己算 weekday | 出错 | system prompt 硬规则 + 工具返回冗余 + 偏离日志（L5 后续）|
| 21 | 模型把 ISO 字符串重新格式化错 | 可能 | 工具返回 `display_cn` 字段，提示词要求"逐字使用" |
| 22 | 模型混淆"上周四"和"7天前的那天" | 可能 | system prompt 显式约定语义 + compare_label 自带说明 |
| 23 | 模型问"4 月有多少个工作日" | 自己数 | 工具返回 `workday_count`，模型只复述 |
| 24 | 用户口语"上周末" / "前天晚上" | 模型解析有歧义 | 短期：模型解析后再用 RequestContext 校验；长期：增加自然语言时间解析工具 |

### 7.5 多 worker / 并发维度

| # | 场景 | 改造后 |
|---|---|---|
| 25 | uvicorn 多 worker | 每个 worker 在启动时独立做 sanity check |
| 26 | 同一请求经过多个 await 点 | RequestContext 不可变，跨 await 安全 |
| 27 | 同一会话多轮对话 | 每轮独立构造 RequestContext，今天/昨天会自动滚动 |
| 28 | 长任务（>5 分钟）期间跨午夜 | RequestContext 在任务入口冻结，回复仍按任务开始时的"今天"，下一次请求自动滚动 |

---

## 8. 测试策略

### 8.1 单元测试矩阵

| 模块 | 测试覆盖 | freeze 时间 |
|---|---|---|
| `TimePoint.from_datetime` | 工作日/周末/节假日/调休/春节窗口/边界月份 | ✓ |
| `DateRange.for_today/yesterday/...` | 各 enum + 跨午夜/跨周/跨月/跨年/闰年 | ✓ |
| `ComparePoint` (wow/mom/yoy/spring_aligned) | 各模式 + 闰年 + 春节漂移 | ✓ |
| `RequestContext.build/for_prompt_injection` | 构造 + 注入字符串格式 | ✓ |
| `relative_label` | 今天/昨天/前天/3天前/上周X/上月X | ✓ |
| `holiday.is_workday/is_holiday/is_spring_window` | 2026 全年关键日期 | - |
| `local_compare_stats` | 各 compare_kind + 各 current_period 组合 | ✓ |
| `_calc_period`（旧路径）| 兼容性回归 | ✓ |

### 8.2 集成测试

| 场景 | 验证 |
|---|---|
| ERPAgent 注入的 system message | freeze 2026-04-10 13:05，断言 message 含 "周五" 和 "ISO 第 15 周" |
| `local_global_stats` 返回顶部时间块 | freeze 时间，断言返回字符串首行格式 |
| `local_compare_stats` 端到端 | mock DB RPC，验证对比标签和数据 |
| 跨午夜请求 | freeze 23:55 → 调用 → freeze 0:05 → 验证 RequestContext 仍是 23:55 |

### 8.3 回归用例（4-10 bug 复现）

```python
@time_machine.travel("2026-04-10 13:05:00 +0800")
async def test_bug_2026_04_10_weekday_label():
    """回归：4-10 同比时不能把 4-3 标成「上周四」"""
    ctx = RequestContext.build("user_x", "org_x")
    assert ctx.today.weekday_cn == "周五"

    result = await local_compare_stats(
        db=mock_db, doc_type="order",
        compare_kind="wow", current_period="today",
    )
    # 当前期
    assert "2026-04-10" in result
    assert "周五（今天）" in result
    # 基线期（上周同期）
    assert "2026-04-03" in result
    assert "周五" in result   # ← 关键：必须是周五
    assert "周四" not in result  # ← 必须不包含周四
    assert "上周同期" in result or "环比上周" in result
```

### 8.4 测试基础设施

`backend/tests/conftest.py` 新增：

```python
import pytest
import time_machine
from datetime import datetime
from zoneinfo import ZoneInfo

@pytest.fixture
def freeze_2026_04_10():
    """Freeze 在 2026-04-10 13:05 周五，复现 bug 时刻。"""
    with time_machine.travel(
        datetime(2026, 4, 10, 13, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        tick=False,
    ):
        yield

@pytest.fixture
def freeze_spring_festival_2026():
    """Freeze 在 2026 春节（2026-02-17 周二，正月初一）。"""
    ...
```

### 8.5 覆盖率目标

- 新增 `time_context.py` / `holiday.py` / `relative_label.py`：**100%**
- 新增 `local_compare_stats.py`：**≥90%**
- 修改 `erp_local_global_stats.py`：增量覆盖率 100%

---

## 9. 迁移与灰度

### 9.1 阶段划分

```
Phase A (基础设施)  → Phase B (核心改造) → Phase C (定时任务) → Phase D (测试) → Phase E (文档)
   ↓                       ↓                    ↓                  ↓
 0.5d                    1.5d                 0.5d              0.5d
```

> **不给精确时间估计，按 V2.2 法律六节"避免给时间估计"。**

### 9.2 兼容性策略

- **L1 RequestContext** 是新增构造，不破坏现有调用
- **L2 工具返回**：`local_global_stats` 返回字符串顶部追加结构化时间块，**保留所有现有字段**，下游格式化测试不破坏
- **L3 prompt** 是文本追加，向后兼容
- **新增工具** `local_compare_stats` 是叠加，不替代旧工具
- **DB 端 RPC**：新增 `erp_compare_stats_query`，不修改 `erp_global_stats_query`

### 9.3 灰度顺序

1. Phase A 上线 → 启动 sanity check 通过 → 不影响业务
2. Phase B 单文件先上 chat_context_mixin / erp_agent 的注入改造（最小风险）
3. Phase B 上 `local_compare_stats` 工具 → 提示词加入 → 模型开始用新工具
4. 验证 1 周无异常 → Phase C
5. 全量回归测试 → Phase D 测试合并

### 9.4 灰度验证

- 每个 phase 单独提交一个 PR
- 每个 PR 必须：
  - 全量后端测试绿
  - 前端测试绿（如有触达）
  - 手动验证 4-10 bug 不复现
  - mypy/ruff 无新增告警

---

## 10. 文档更新

| 文档 | 更新内容 | 触发节点 |
|---|---|---|
| `PROJECT_OVERVIEW.md` | 新增 `backend/utils/` 模块说明 | Phase A 完成 |
| `FUNCTION_INDEX.md` | 收录 `RequestContext` / `TimePoint` / `DateRange` / `ComparePoint` / `local_compare_stats` | Phase B 完成 |
| `TECH_ERP本地优先统一查询架构.md` | 在 §6 工具章节追加"时间事实层规范"链接 | Phase B 完成 |
| `TECH_Agent架构安全层补全.md` | 引用本文为 L1 SSOT 实现 | Phase A 完成 |
| `CURRENT_ISSUES.md` | 记录 4-10 bug 闭环 | 全部完成后 |
| `~/.claude/.../memory/MEMORY.md` | 添加"时间事实层架构 — 2026-04-10"决策记录 | Phase B 完成 |

---

## 11. 回滚方案

### 11.1 回滚原则

每个 Phase 独立 PR，可独立回滚。

### 11.2 各 Phase 回滚成本

| Phase | 回滚成本 | 备注 |
|---|---|---|
| A | 极低 | 新建文件，删除即可；启动 sanity check 改为 warning 而非 fail-fast |
| B-prompt | 极低 | 单行恢复 |
| B-工具新增 | 低 | 删除工具定义，模型回退到旧 `local_global_stats` 双调 |
| B-工具改造 | 中 | 旧字段保留，移除新结构化块即可 |
| C 定时任务 | 低 | 单行恢复 |
| Migration 057 | 中 | DROP FUNCTION 即可，不影响数据 |

### 11.3 灾难恢复

如启动 sanity check fail-fast 阻止服务启动：
1. 立即设置环境变量 `SKIP_TIME_SANITY_CHECK=1`
2. 服务启动后排查 tzdata / TZ 问题
3. 修复后移除环境变量

---

## 12. 不在范围内的事

明确不做，避免范围蔓延：

- ❌ **多租户多时区**：org.timezone 字段、跨时区显示
- ❌ **ERP 数据表 TIMESTAMP → TIMESTAMPTZ 迁移**：数据迁移风险，单独任务
- ❌ **erp_sync_worker.py / executor.py / dead_letter.py 内部 30+ 处 naive datetime**：仅加 TODO 标记
- ❌ **农历↔公历转换工具**：仅做"春节窗口标记"，不做农历日期
- ❌ **L4 输出层正则校验**：先做 L1+L2+L3，看实际幻觉率再决定是否补 L4
- ❌ **L5 偏离日志看板**：同上
- ❌ **国际化（英文 weekday）**：当前产品仅服务中国用户
- ❌ **历史时区规则（夏令时/LMT）**：业务数据均在 2020+
- ❌ **企微定时推送任务的时区改造**：留作企微 Agent 独立任务

---

## 13. 决策记录（已锁定）

> 与用户讨论后于 2026-04-10 锁定，进入执行阶段。

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| D1 | 部署方式 | 远程 SSH 直接运行 venv（非 Docker） | 已确认仓库无 Dockerfile，`deploy/deploy.sh` 走 rsync + ssh |
| D2 | 生产服务器当前 TZ | **CST（北京时间）** ✓ | 已 SSH 验证：`date` 返回 `Fri Apr 10 17:43:05 CST 2026`，`time.tzname=('CST','CST')`，**通过 `/etc/localtime` 软链设置**。但 `TZ` env 未设置，需补强 |
| D3 | L0 实现位置 | **systemd unit 加 `Environment=TZ=Asia/Shanghai`** + 部署脚本 export 兜底 | 远程部署，无 Docker |
| D4 | 同比/环比默认语义 | **公历对齐**（不做春节对齐默认）| 用户明确要求："公历就是正常我们大家在用的这种时间" |
| D5 | 春节对齐 | 仅作为 enum `spring_aligned` 保留，不做默认 | 业务先简单，需要再启用 |
| D6 | 「上周」语义 | **ISO 周（周一为始）的上一周** | 中文电商约定 |
| D7 | chinese-calendar 升级提醒 | **方案 c：启动时运行时检查**（库覆盖年份 < 当前+1 时打 warning 日志） | 零额外维护，大厂做法 |
| D8 | 测试冻结时间库 | **`time-machine`**（不是 freezegun） | asyncio + zoneinfo + Pydantic v2 兼容更好 |
| D9 | 4-10 bug 实际根因 | **纯 LLM 幻觉**，与 TZ **无关** | 服务器是 CST，`%A` 输出 Friday 没错，是模型把 Friday 理解成"周四" |
| D10 | 范围 — L0+L1+L2+L3 | ✅ 做 | 解决业务正确性 |
| D11 | 范围 — L4 事实校验中间件 | ✅ 做 | **复用已有 agent loop 装饰器位置**，边际成本低 |
| D12 | 范围 — L5 偏离日志 | ✅ 做 | **复用 Phase 6 结构化审计日志，不建新表** |
| D13 | 范围 — sync_worker 内部 30+ 处 `datetime.now()` | ❌ 不做 | 仅加 TODO，独立任务 |
| D14 | 范围 — DB `TIMESTAMP→TIMESTAMPTZ` 迁移 | ❌ 不做 | 数据风险高 |
| D15 | 范围 — 多租户多时区 | ❌ 不做 | 当前所有 org 都在中国 |
| D16 | 范围 — 农历转换 | ❌ 不做 | 用户明确："不是农历是公历" |
| D17 | 执行顺序 | **3 个 PR** | PR1=L0~L3+compare 工具；PR2=L4+L5；PR3=测试+文档 |
| D18 | 方案锁定后再做一次最终扫描 | ✅ 必须 | 用户要求："确认方案以后还得扫描一下，不要漏掉" — 见 §15 |

---

## 14. L4/L5 与现有安全层的耦合

> **实施状态**：✅ PR2 已完成（2026-04-11）。详见 §14.6 实施结果。

### 14.1 架构定位

L4/L5 不是独立子系统，而是 [TECH_Agent架构安全层补全.md](TECH_Agent架构安全层补全.md) 的 **Phase 7：事实正确性 Guardrail**。

```
现有安全层（Phase 1-6）：
  Phase 1  ToolResultEnvelope     ← 工具输出截断/信号
  Phase 2  ContextCompression     ← 上下文压缩
  Phase 3  ExecutionBudget        ← 全局时间预算
  Phase 4  QueryCache             ← 请求去重
  Phase 5  WriteIdempotency       ← 写操作幂等
  Phase 6  StructuredAuditLog     ← 结构化审计日志

新增：
  Phase 7  FactualGuardrail       ← 事实正确性校验（L4 + L5）
    ├── L4 TemporalValidator      ← 校验日期/星期一致性
    └── L5 DeviationLog           ← 复用 Phase 6 写入，type='fact_deviation'
```

### 14.2 与 Phase 6 审计日志的复用

**Phase 6 已规划的表**（[TECH_Agent架构安全层补全.md §6](TECH_Agent架构安全层补全.md)）：
- `tool_audit_log`（已存在）

**L5 复用方式**：
- **不建新表**
- 偏离记录写入既有的结构化审计日志，新增 `audit_type = 'fact_deviation'`
- 字段映射：
  ```
  tool_name      → "temporal_validator"
  status         → "deviation_detected" / "auto_patched"
  args_hash      → 原始模型输出的 hash
  result_length  → snippet 长度
  其他字段        → 偏离详情存 args（claimed/actual/snippet）
  ```

### 14.3 L4 接入点

- **位置**：[erp_agent.py:341](backend/services/agent/erp_agent.py#L341) `_run_tool_loop` 返回 `accumulated_text` 之前
- **形式**：
  ```python
  from services.agent.guardrails import temporal_validator
  
  if accumulated_text:
      accumulated_text, deviations = temporal_validator.validate(
          accumulated_text, ctx=request_context,
      )
      if deviations:
          # 复用 Phase 6 审计日志
          self._emit_fact_deviation_audit(deviations)
  ```

### 14.4 失败处理策略

| 偏离类型 | 处理方式 | 配置项 |
|---|---|---|
| weekday_mismatch（星期错）| **自动 patch**（轻）| 默认开启 |
| date_mismatch（日期数字错）| **重生成**（重，让模型再算一次）| 默认关闭，需要时启用 |
| 多次重生成仍失败 | 加 system message 降级提示用户 | 默认开启 |

### 14.5 可拓展性

Phase 7 设计支持未来加更多 validator：
- `NumericValidator` — 校验"比上周多 1186 笔"是否与工具返回的数据一致
- `EnumValidator` — 校验枚举值（如平台名）
- `IDValidator` — 校验商品/订单/店铺 ID 格式

本次只实现 `TemporalValidator`，框架预留接口。

### 14.6 PR2 实施结果（2026-04-11 已完成）

#### 新建文件

| 文件 | 职责 | 行数 |
|---|---|---|
| [`backend/services/agent/guardrails/__init__.py`](backend/services/agent/guardrails/__init__.py) | 包导出 | ~25 |
| [`backend/services/agent/guardrails/temporal_validator.py`](backend/services/agent/guardrails/temporal_validator.py) | L4 正则扫描 + patch | ~290 |
| [`backend/services/agent/guardrails/fact_deviation_log.py`](backend/services/agent/guardrails/fact_deviation_log.py) | L5 双写（audit log + loguru） | ~95 |
| [`backend/tests/test_temporal_validator.py`](backend/tests/test_temporal_validator.py) | L4 单测（22 个） | ~230 |
| [`backend/tests/test_fact_deviation_log.py`](backend/tests/test_fact_deviation_log.py) | L5 单测（6 个） | ~155 |
| [`backend/tests/test_erp_agent_l4_integration.py`](backend/tests/test_erp_agent_l4_integration.py) | L4 集成测试（4 个） | ~90 |

#### 修改文件

- [`backend/services/agent/erp_agent.py`](backend/services/agent/erp_agent.py) — `_run_tool_loop` 返回前插入 L4 校验 + L5 日志

#### 关键决策记录

1. **L5 双写策略（不加 migration 057）**
   - DB：`tool_audit_log` 写虚拟工具 `tool_name="temporal_validator"`，状态 `auto_patched`/`deviation_detected`
   - loguru：结构化详情 `logger.bind(component="fact_deviation", ...)` 供 grep 排查
   - 原因：零 schema 变更，风险最低

2. **L4 只在 is_llm_synthesis=True 且非 ask_user 时触发**
   - 正常合成 / route_to_chat → ✅ 走 L4
   - ask_user（追问）/ 循环检测 / token 超限 → ❌ 不走 L4（原始数据不应被当成断言校验）

3. **Patch 保留原前缀风格**
   - claimed="星期二" → actual="星期一"（不是"周一"）
   - claimed="礼拜六" → actual="礼拜五"
   - 尊重用户原文表达

4. **正则 overlapping 过滤**
   - 两个正则（日期→星期、星期→日期）产生重叠时，按 start 排序丢弃相交区间
   - 防止 "4月3日周四 和 4月7日周三" 被当成单个跨越匹配

5. **Connector 禁数字/日月/周星礼**
   - 防止"4月3日（周四）和 4月7日" 的 connector 吃掉第二个日期
   - 支持 `2026-04-10T13:05:00 周四` 等 ISO 时间格式（时间部分被吸收进 date core）

6. **跳过规则**
   - markdown 代码块（```...```）
   - "例如/比如/假设/举例" 等上下文标记（±30 字符前置窗口）
   - 跨换行和分句标点

#### 测试覆盖

- **L4 单元**：22 个（正确/错误/边界/鲁棒性 4 类）
- **L5 单元**：6 个（空/单/多/状态/DB 失败/loguru 绑定）
- **L4 集成**：4 个（4-10 bug 原文端到端 / 时间块保护 / 等）
- **总量**：32 → 34（自审时发现 ISO 时间 bug 补 2 个）
- **全量回归**：3398 passed, 7 skipped（PR1 基线 3364 + PR2 新增 34）

#### 4-10 Bug 双层防护验证

现在 4-10 bug 有两层保护：

| 层级 | 效果 | 生效时机 |
|---|---|---|
| **L1~L3（PR1）** | 工具返回带 `weekday_cn`，prompt 硬规则 | 模型读到事实就不会算错 |
| **L4（PR2）** | 输出层正则扫描，自动 patch `周四→周五` | 模型违反硬规则自己算错时兜底 |

两层独立，互为冗余。

---

## 15. 方案锁定后的最终扫描清单

> **法律 2.2 节强化**：方案确认后，编码前必须再做一轮全项目扫描，确保改动清单完整无遗漏。

### 15.1 扫描维度

| # | 扫描目标 | 命令 / 方法 | 验收标准 |
|---|---|---|---|
| S1 | 全项目所有 `datetime.now()` / `date.today()` / `time.localtime()` 裸调用 | `grep -rn "datetime\.now()\|date\.today()\|time\.localtime()" backend/` | 每个匹配都被打 TODO 或纳入改动 |
| S2 | 所有 prompt 注入"当前时间"的位置 | `grep -rn "当前时间\|今天是\|now_str\|current.*time" backend/services/` | 每个注入点都被改成 RequestContext |
| S3 | 所有 system prompt 拼接代码（不限时间） | `grep -rn "role.*system\|messages.*append.*system" backend/` | 找出所有可能注入时间的地方，确认是否被覆盖 |
| S4 | 所有解析用户日期输入的函数 | `grep -rn "fromisoformat\|strptime\|parse_date" backend/` | 确认时区处理一致 |
| S5 | 所有 ERP 工具的返回字符串生成位置 | `grep -rn "return.*\\\\n\|\\\\n\".*join" backend/services/kuaimai/erp_local_*.py` | 确认是否需要加结构化时间块 |
| S6 | 所有 LLM 工具调用的入口（前端/企微/Web）| `grep -rn "ERPAgent\|chat_service\|wecom" backend/` | 确认 RequestContext 注入覆盖所有入口 |
| S7 | 所有定时任务/cron/scheduler | `grep -rn "scheduler\|cron\|@scheduled\|asyncio.create_task.*sleep" backend/` | 确认是否使用了 naive datetime |
| S8 | 测试中所有"日期/时间"断言 | `grep -rn "weekday\|isoformat\|strftime\|2026-\|周一\|周五" backend/tests/` | 确认是否需要 freeze time |
| S9 | DB 迁移文件中的时间列定义 | `grep -rn "TIMESTAMP\|timestamptz\|date" backend/migrations/*.sql` | 列出需要后续迁移的表 |
| S10 | 前端代码中显示日期/星期的位置 | `grep -rn "weekday\|周一\|format.*date" frontend/src/` | 确认前端是否需要配合改造 |
| S11 | 所有依赖 OS 时区的库 | `grep -rn "loguru\|logging.Formatter\|asctime" backend/` | 确认 systemd 设 TZ 后是否生效 |
| S12 | 测试 fixture / conftest 现有 mock 时间方式 | `grep -rn "freeze\|patch.*datetime\|mock.*now" backend/tests/conftest.py backend/tests/` | 确认是否冲突 |
| S13 | 已有的 chinese-calendar / 节假日相关代码 | `grep -rn "chinese_calendar\|holiday\|工作日\|节假日" backend/` | 确认是否有重复实现 |
| S14 | requirements.txt 现有依赖冲突 | `pip-compile --dry-run` | 确认 tzdata/chinese-calendar/time-machine 与现有依赖兼容 |

### 15.2 扫描产出

扫描报告（`/tmp/time_arch_final_scan.md`，临时文档），包含：

1. **遗漏的改动点**（按文件 + 行号）
2. **已被现有改动清单覆盖的点**（确认无重复）
3. **新发现的边界场景**（追加到 §7）
4. **新发现的依赖风险**（追加到 §11）
5. **修订后的改动清单**（更新 §6）

### 15.3 扫描通过标准

- ✅ S1-S14 全部完成
- ✅ 遗漏改动点 = 0（如有，更新 §6 后再次扫描）
- ✅ 用户确认扫描报告
- ✅ 然后才进入 PR1 编码

### 15.4 扫描的"非目标"

- ❌ 扫描期间不动代码
- ❌ 不做"顺手优化"
- ❌ 不重新质疑已锁定的决策（D1-D18）

---

## 16. 执行顺序（PR 划分）

### PR1 — 业务正确性核心

| Phase | 内容 | 产物 |
|---|---|---|
| Phase A | 基础设施（time_context / relative_label / holiday / utils 包） | 8 个新文件 |
| Phase B 部分 | erp_agent / chat_context_mixin / erp_local_global_stats / local_compare_stats / formatters/common.py | 7 个文件改造 |
| Phase L0 | systemd unit + deploy/config.env 加 TZ | 2 个文件改造 |
| 验收 | 4-10 bug 回归测试通过 + 全量测试绿 | - |

### PR2 — 安全层 Phase 7

| Phase | 内容 | 产物 |
|---|---|---|
| L4 | TemporalValidator 中间件 | guardrails/temporal_validator.py |
| L5 | 偏离日志（复用 Phase 6 audit log） | 修改 erp_agent.py + tool_audit.py |
| 文档 | TECH_Agent架构安全层补全.md 追加 Phase 7 章节 | 1 个文件改造 |
| 验收 | 注入幻觉测试 → L4 抓到并 patch → L5 写入 audit log | - |

### PR3 — 测试 + 文档 + 定时任务清理

| Phase | 内容 | 产物 |
|---|---|---|
| Phase D | time-machine + 边界测试（跨午夜/月/年/闰年/春节窗口）| 3 个测试文件 |
| Phase C | sync_reconcile + scheduler 修复 | 2 个文件改造 |
| Phase E | 文档同步（PROJECT_OVERVIEW / FUNCTION_INDEX / MEMORY） | 3 个文档 |
| 测试迁移 | `test_erp_sync_scheduler.py` 等 5+ 文件硬编码日期改 time-machine | 5 个测试改造 |
| TODO 标记 | sync_worker 等内部 30+ 处加 `# TODO(time-context)` | 标记不动 |
| 验收 | 测试覆盖率 ≥ 90% on 新代码 | - |

---

## 17. 最终扫描结果与方案修订（2026-04-10 完成）

> 4 路并行扫描覆盖 §15 全部 14 个维度，输出对原方案的修订。

### 17.1 扫描覆盖率

| 维度 | 工具 | 状态 |
|---|---|---|
| S1 (datetime.now 裸调用) | Explore Agent #1 | ✓ 完成 |
| S2 (prompt 时间注入) | Explore Agent #1 | ✓ 完成 |
| S3 (system message 拼接) | Explore Agent #1 | ✓ 完成 |
| S4 (日期解析函数) | Explore Agent #1 | ✓ 完成 |
| S5 (ERP 工具返回) | Explore Agent #2 | ✓ 完成 |
| S6 (LLM 入口) | Explore Agent #2 | ✓ 完成 |
| S7 (定时任务) | Explore Agent #1 | ✓ 完成 |
| S8 (测试断言) | Explore Agent #3 | ✓ 完成 |
| S9 (DB 时间列) | Explore Agent #4 | ✓ 完成 |
| S10 (前端日期显示) | Explore Agent #4 | ✓ 完成 |
| S11 (loguru/logging 时区) | Explore Agent #2 | ✓ 完成 |
| S12 (mock 时间 fixture) | Explore Agent #3 | ✓ 完成 |
| S13 (节假日代码) | Explore Agent #4 | ✓ 完成 |
| S14 (依赖兼容) | Explore Agent #3 | ✓ 完成 |

### 17.2 关键新发现

#### 🔴 P0 紧急（升级到 PR1 第一批）

| 编号 | 文件 | 问题 | 风险 |
|---|---|---|---|
| **N1** | [client.py:181/192/271](backend/services/kuaimai/client.py#L181) | 快麦 API 签名时间戳 `datetime.now().strftime()` 无时区 | **服务器 TZ 漂移会导致所有快麦 API 请求被签名校验拒绝，整个 ERP 同步直接挂掉**。这比 4-10 weekday bug 严重得多 |

→ 已纳入 §6.1 改动 **A0**

#### 🟡 P1 范围扩大

| 编号 | 内容 | 之前估计 | 实际 |
|---|---|---|---|
| **N2** | 需要加结构化时间块的 ERP 工具 | 1 个 | **8 个**（详见 §6.2.2 表 B5a-h）|
| **N3** | systemd unit 改造点 | 估计需要找 | ✓ 已定位：`deploy/everydayai-backend.service:14` 和 `deploy/everydayai-wecom.service:13` |
| **N4** | 企微回调入口缺 RequestContext 注入 | 未识别 | ✓ 已定位：`wecom.py:62` + `wecom.py:97` → 详见 §6.2.4 B13/B14 |
| **N5** | loguru 日志依赖 OS TZ | 未识别 | `logging_config.py:36-42` 用 loguru `{time:...}` — systemd 设 TZ 后**自动生效，无需改代码** |

#### 🟢 利好（之前不知道的好消息）

| 编号 | 内容 | 影响 |
|---|---|---|
| **G1** | 项目已有 `OrgCtx` 依赖注入（`api/routes/message.py:131` 等）| RequestContext 直接复用 OrgCtx 模式，**不用从零设计** |
| **G2** | `POST /message/generate` / `POST /conversation` 已有 OrgCtx | 主入口改造点最小（仅升级 OrgCtx → RequestCtx）|
| **G3** | 现有 mock 时间方式仅 1 处 `@patch("datetime")` | 迁移到 `time-machine` 成本极小 |
| **G4** | 节假日相关代码全项目 0 命中 | 引入 `chinese-calendar` **零冲突** |
| **G5** | 前端只 2 处小动（`conversationUtils.ts` + `SettingsModal.tsx`）| 与 ERP 完全无关，**前端可独立 / 并行做** |
| **G6** | 3 个新依赖兼容性 ✓ | tzdata / chinese-calendar / time-machine 全部零冲突 |
| **G7** | Pydantic v2.10.4 已支持 `AwareDatetime` | 类型校验现成可用 |

#### 📋 P2 工程清理（加 TODO，不在本次范围）

| 编号 | 内容 | 处理 |
|---|---|---|
| **T1** | sync 层 7 处 `datetime.now()` 时间戳（dead_letter / config_handlers / service / worker / executor / piggyback / formatters/common.py:114）| 仅加 `# TODO(time-context)` 标记 |
| **T2** | erp_document_items 6 个 TIMESTAMP 列（doc_created_at / pay_time / consign_time / delivery_date / finished_at / doc_modified_at）| 列入"后续独立迁移单"，不在本次 |

#### 📝 PR3 测试改造工作量

| 编号 | 内容 | 工作量 |
|---|---|---|
| **TST1** | 5+ 硬编码日期测试改 time-machine（test_erp_sync_scheduler / test_erp_sync_handlers / test_erp_sync_reconcile / test_model_scorer）| 1.5 小时 |
| **TST2** | 1 处 `@patch("datetime")` 迁移到 `@time_machine.travel()` | 30 分钟 |

### 17.3 阻塞性发现

**没有阻塞性发现**。所有发现都是"扩大范围"和"细化定位"，没有任何东西推翻已锁定的 18 项决策（D1-D18）。

### 17.4 修订后的扫描通过标准

- ✅ S1-S14 全部完成
- ✅ 遗漏改动点已合并到 §6（A0 + B5a-h + B11-B16 + A7/A7b/A7c）
- ✅ 风险点已分类（P0/P1/P2）
- ✅ 利好点已计入工作量估算（OrgCtx 复用降低成本）
- ⏳ **待用户确认 §17 后进入 PR1 编码**

---

## 附录 A：调研引用清单

### 学术
- [Date Fragments — arxiv 2505.16088](https://arxiv.org/abs/2505.16088)
- [Temporal Blindness — arxiv 2510.23853](https://arxiv.org/abs/2510.23853)
- [Test of Time Benchmark — arxiv 2406.09170](https://arxiv.org/html/2406.09170v1)
- [TimeBench — ACL 2024](https://github.com/zchuz/TimeBench)

### 行业方案
- [Looker dimension_group 参考](https://cloud.google.com/looker/docs/reference/param-field-dimension-group)
- [Looker Period-over-Period](https://cloud.google.com/looker/docs/period-over-period)
- [阿里 Quick BI 日期查询控件](https://help.aliyun.com/zh/quick-bi/user-guide/query-data-based-on-a-date-field)
- [ThoughtSpot Sage Docs](https://docs.thoughtspot.com/cloud/latest/search-sage)

### 官方
- [Anthropic Claude System Prompts Release Notes](https://platform.claude.com/docs/en/release-notes/system-prompts)
- [Claude Code Issue #24182 — per-message timestamp](https://github.com/anthropics/claude-code/issues/24182)
- [Gemini Function Calling Docs](https://ai.google.dev/gemini-api/docs/function-calling)
- [PEP 615 — zoneinfo](https://peps.python.org/pep-0615/)
- [Python zoneinfo Data sources](https://docs.python.org/3/library/zoneinfo.html#data-sources)

### 工程
- [chinese-calendar — LKI/chinese-calendar](https://github.com/LKI/chinese-calendar)
- [time-machine — adamchainz/time-machine](https://github.com/adamchainz/time-machine)
- [Pydantic v2 datetime types](https://docs.pydantic.dev/latest/api/standard_library_types/#datetimedatetime)

---

## 附录 B：关键代码片段（伪代码示例）

### B.1 `RequestContext.build` 在 HTTP 入口的注入

```python
# backend/api/routes/chat.py（示意）
@router.post("/chat")
async def chat_endpoint(req: ChatRequest, current_user: User = Depends(...)):
    ctx = RequestContext.build(
        user_id=current_user.id,
        org_id=current_user.org_id,
        request_id=req.request_id,
    )
    # ctx 全程不可变，下游所有 await 共享同一个 now
    return await chat_service.handle(req, ctx=ctx)
```

### B.2 `local_compare_stats` 顶部时间块格式

```
[当前期] 2026-04-10 周五（今天） 00:00–13:05 北京时间
[基线期] 2026-04-03 周五（环比上周同期） 00:00–13:05 北京时间
[对比模式] 环比上周（wow） · 语义：上周指 ISO 周一至周日的上一周

订单数：1,769 笔（当前期） vs 2,955 笔（基线期）
环比变化：-1,186 笔（-40.1%）

[同步状态] ✓ 已同步至 13:04
```

### B.3 ERP_ROUTING_PROMPT 新增硬规则

```
## 时间事实使用规范（强制）
- 工具返回的 [当前期]/[基线期] 时间块必须**逐字复述**，不要重新格式化日期或星期
- 禁止自行推算"4月X日是周几"——直接读工具返回的中文星期
- 禁止自行计算"上周X日是哪天"——必须用 local_compare_stats 工具
- "上周/上月/同比/环比"必须调 local_compare_stats，禁止调 local_global_stats 两次拼对比
- 所有时间术语遵循约定：
  - 上周 = ISO 周（周一为始）的上一周
  - 同比 = 去年同期
  - 环比 = 上一周期同位置
```

---

**文档结束**
