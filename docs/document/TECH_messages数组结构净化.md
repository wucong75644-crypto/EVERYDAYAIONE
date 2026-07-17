# messages 数组结构净化方案

> **独立项目**:与文件路径协议优化并列(不耦合)
> **目的**:消除 attachments XML 嵌入 user message 造成的元数据污染
> **核心改造**:`attachments` 元数据从 user content 拆到独立 system message + 历史加载自动剥离
> **规模**:小型(~15 行代码改动 + 1 个新函数)
> **预估**:3-5 天(设计 + 实现 + 验证 + 灰度)

---

## 0. 问题陈述

### 0.1 当前实现

[chat_context_mixin.py:229-231](backend/services/handlers/chat_context_mixin.py#L229):
```python
# Layer 7: 用户消息(始终最后)
_user_text = text_content
if workspace_files:
    _user_text += self._format_attachments(workspace_files, conversation_id)
messages.append({"role": "user", "content": _user_text})
```

attachments XML **追加到 user text 末尾**,跟用户原话拼成一段字符串发给 LLM,并落 DB。

### 0.2 LLM 实际看到的 user message

**用户输入**:
```
分析下账单
```

**发给 LLM 的 user content**(200+ 字符):
```
分析下账单

<attachments count="1" hint="status 字段是行动指引;每个文件按 status 决定下一步操作">
  <file>
    <name>账单.xlsx</name>
    <type>数据文件</type>
    <format>xlsx</format>
    <size>2.1MB</size>
    <source>本轮上传</source>
    <status>已分析。直接在 code_execute 中用 get_file("账单.xlsx") + duckdb 查询。</status>
  </file>
</attachments>
```

### 0.3 真实异味(3 个层次)

#### 异味 1:语义混淆
LLM 解析 user message 时,本能把整段视为"用户输入"。但其中 200+ 字符是**系统注入的元数据**,而非用户字面意图。

- 用户字面意图:4 个字
- 元数据 XML:200+ 字符(用户实际不知道这段存在)
- LLM 注意力被元数据**稀释**,容易把"附件指引"当成"用户要求"

#### 异味 2:跨轮历史污染
turn 1 的 attachments XML 落 DB 后,turn 2/3/4 加载历史时仍在 user message 里。LLM 看 history:

```
[turn 1 user]:  分析账单 + <attachments>...</attachments>   (200 字)
[turn 1 ai]:    ...
[turn 2 user]:  按月聚合一下 + <attachments>...</attachments>  (新一份 XML)
[turn 2 ai]:    ...
[turn 3 user]:  ...
```

每轮都堆叠 XML,**累积 token**,且 LLM 误以为"用户每次都输入 XML"。

#### 异味 3:压缩 / 截断时丢失意义
长对话触发 history budget 截断时,旧 user message 直接消失。但其中混了 XML 元数据,**摘要质量受影响**(LLM 摘要时被 XML 拉偏)。

---

## 1. 行业对照(为什么这是个真问题)

### 1.1 OpenAI Assistants API
```
Thread 维度持有 files,messages 数组里 user content 是纯净的:
{
  "thread_id": "thread_abc",
  "files": ["file-123", "file-456"]
}

[{"role": "user", "content": "分析账单"}]    ← 永远干净
```

调用 LLM 时 API 内部 prepend system message:
> "User has attached files: account.xlsx at /mnt/data/account.xlsx ..."

### 1.2 Anthropic Claude API(2024+)
```
user content 用结构化 blocks(文件独立):
{
  "role": "user",
  "content": [
    {"type": "document", "source": {...}, "title": "账单.xlsx"},
    {"type": "text", "text": "分析账单"}
  ]
}
```

**文件 = 独立 block,跟 text 同级**,语义清晰。

### 1.3 ChatGPT Code Interpreter
- 文件落 VM `/mnt/data/`(持久)
- LLM 第一轮看 system message 注入"用户上传了 sales.csv"
- 后续轮**不依赖 messages 里的记忆**,用 `ls /mnt/data/` 实时探索
- **user message 数组里永远是用户字面输入**

### 1.4 Gemini / Google AI
类似 Claude — 文件作为 `Part`,跟 text 同级。

### 1.5 共识

| 共识 | 我们当前 | 偏离行业 |
|---|---|---|
| user message 是用户**纯净表达** | ❌ 混 XML | 是 |
| 文件元数据**独立存放**(block/system/thread) | ❌ 嵌 user text | 是 |
| 跨轮**不依赖** messages 里的旧元数据 | ⚠️ 历史里 XML 堆叠 | 是 |
| 跨轮持久 → 文件系统探索 或 server-side thread.files | ⚠️ 没机制 | 是 |

---

## 2. 设计方案

### 2.1 改造点 1:发给 LLM 时,attachments 改 system message 注入

**改前**([chat_context_mixin.py:225-244](backend/services/handlers/chat_context_mixin.py#L225)):
```python
# Layer 7: 用户消息(始终最后)
_user_text = text_content
if workspace_files:
    _user_text += self._format_attachments(workspace_files, conversation_id)  # ← 混入 user text

user_msg: Dict[str, Any] = {"role": "user", "content": _user_text}
# ... image_urls / file_urls 处理 ...
messages.append(user_msg)
```

**改后**:
```python
# Layer 6.7: 当前轮附件元数据(独立 system message,紧贴 user 前)
if workspace_files:
    attachments_xml = self._format_attachments(workspace_files, conversation_id)
    if attachments_xml.strip():
        messages.append({"role": "system", "content": attachments_xml})

# Layer 7: 用户消息(纯净)
user_msg: Dict[str, Any] = {"role": "user", "content": text_content}  # ← 不再追加 XML
if image_urls or file_urls:
    media_parts = [...]
    user_msg["content"] = [
        {"type": "text", "text": text_content},  # ← 用纯净文本
        *media_parts,
    ]
messages.append(user_msg)
```

效果:
- LLM 看 user message 永远是用户字面输入
- attachments 元数据作为独立 system,语义角色分明
- 跟 Claude API 哲学对齐(等价于 system message 替代 content block)

### 2.2 改造点 2:加载历史时剥离旧 attachments XML

**新增函数**(放 `chat_context/content_extractors.py`):
```python
_ATTACHMENTS_RE = re.compile(
    r'\n*<attachments[^>]*>.*?</attachments>\s*',
    flags=re.DOTALL,
)

def strip_attachments_xml(text: str) -> str:
    """剥离 user message 里的历史 attachments XML(只在加载历史时用)。

    DB 存的是完整 user content(含 XML,作审计/导出用),
    加载发给 LLM 时,移除 XML 让历史 user message 保持纯净。
    """
    if not text or "<attachments" not in text:
        return text
    return _ATTACHMENTS_RE.sub("", text).rstrip()
```

**接入点**(`extract_text_from_content` 末尾):
```python
def extract_text_from_content(content: Any) -> str:
    # ... 现有逻辑 ...
    text = ...  # 提取出的文本
    return strip_attachments_xml(text)  # ← 加这一行
```

效果:
- DB 存储不变(向后兼容,审计/导出仍能拿到完整记录)
- LLM 看到的历史 user message 自动剥离 XML
- 不影响 attachments 在当前轮的注入

### 2.3 改造点 3:保留向后兼容

- DB 存储格式**不变**(user content 仍包含 attachments XML,旧数据无需迁移)
- 旧版本前端 / 客户端不受影响(只是 LLM 看到的 message 结构变了)
- 可以**一键回滚**:feature flag 控制是否走新路径

---

## 3. 实施步骤

### Phase 1:代码实现(1 天)

| Step | 文件 | 改动 |
|---|---|---|
| 1 | `chat_context/content_extractors.py` | 新增 `strip_attachments_xml(text)` 函数 + 接入 `extract_text_from_content` |
| 2 | `chat_context_mixin.py:_build_llm_messages` | attachments 从 user_text 追加 → 改为独立 system message |
| 3 | `core/config.py` | 加 feature flag `messages_attachments_as_system: bool = False` |
| 4 | `_build_llm_messages` | feature flag 控制走新/旧路径(灰度期共存) |

### Phase 2:单元测试(0.5 天)

| 测试 | 验证 |
|---|---|
| `test_strip_attachments_xml` | 各种 XML 边界(嵌套/带属性/多个 file)正确剥离 |
| `test_build_messages_new_path` | feature flag=true 时,attachments 在 system,user 纯净 |
| `test_build_messages_legacy_path` | feature flag=false 时,行为不变(向后兼容) |
| `test_history_load_strips_attachments` | 加载历史时旧 XML 自动剥离 |
| `test_db_storage_unchanged` | DB 存储仍是完整内容 |

### Phase 3:真实数据验证(1-2 天)

| 验证项 | 验证方式 | 通过标准 |
|---|---|---|
| token 节省(单轮) | 同一对话 feature flag on/off,统计 messages 总 token | 节省 ≥ 10%(单轮 attachments ~200 字符)|
| token 节省(长对话) | 10 轮对话,统计累计 token | 节省 ≥ 30%(每轮 XML 累积消除)|
| LLM 回复质量 | 5 个真实场景,人工对比 on/off 的回复 | 主观评分相当或更好 |
| 长对话连续性 | turn 1 上传文件,turn 10 引用 | AI 仍能找到文件(走 file_search/ls 探索)|
| 历史摘要质量 | 触发摘要场景,对比 on/off 的摘要内容 | 摘要更聚焦用户意图,不含 XML 噪声 |

### Phase 4:灰度上线(2-3 天)

| Phase | 范围 | 时长 | 退出标准 |
|---|---|---|---|
| 4a | 内部账号 | 0.5 天 | 5 场景通过 |
| 4b | 5% 用户 | 1 天 | 关键指标无退化 |
| 4c | 50% | 1 天 | 同上 |
| 4d | 100% | — | 观察 1 周 |
| 4e | 清理 feature flag | — | — |

---

## 4. 真实数据验证矩阵

### 4.1 token 节省测试(可量化)

**测试方法**:
```python
# 选 10 个真实 conversation(含文件附件),分别用新旧路径构造 messages
# 统计 total_tokens 差异

baseline = build_messages_legacy(conv)
new_path = build_messages_new(conv)
print(f"saved: {len(baseline.tokens) - len(new_path.tokens)}")
```

**预期**:
- 单轮:节省 200-500 token(一份 attachments XML 大小)
- 5 轮:节省 1000-2500 token(累积消除历史 XML)
- 10 轮:节省 2000-5000 token

### 4.2 LLM 行为对比(主观 + 客观)

**场景 1:简单分析**
- 用户:上传账单,问"分析下"
- 对比 on/off 的回复在:
  - 是否更直接回应用户意图(不被 XML 元数据干扰)
  - 是否仍正确调 file_analyze 等工具(指引仍生效)

**场景 2:跨轮引用**
- turn 1:上传账单
- turn 5:引用"那个账单"
- 验证 AI 是否仍能定位文件(系统注入 + file_search 兜底)

**场景 3:多文件**
- 上传 3 个文件,问"对比这三个"
- 验证 attachments 多文件渲染清晰

**场景 4:长对话**
- 20 轮对话,中间触发 history budget 截断
- 验证 AI 是否仍能定位 turn 2 上传的文件(走 file_search)

**场景 5:误传调试**
- 用户上传时输入了一段 XML 形似内容(罕见但可能)
- 验证 strip 函数不会误剥用户字面输入(`<attachments>` 必须是系统生成格式)

### 4.3 关键指标看板

| 指标 | baseline | 阈值(新方案 vs baseline) |
|---|---|---|
| 平均 token/对话 | 实测 | ↓ 15-30% |
| 平均回复时长 | 实测 | ≤ 105%(不退化) |
| LLM 调用 file_analyze 比例 | 实测 | 保持 ≥ 90%(指引仍生效) |
| 跨轮引用文件成功率 | 实测 | ≥ 95%(file_search 兜底) |
| 用户投诉率 | 实测 | 无新增 |

---

## 5. 风险清单

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| attachments 改 system 后 LLM 不识别附件指引 | 低 | 中 | system message 内容不变,只是位置变;LLM 训练充分处理 system |
| 历史剥离误剥用户字面 XML | 极低 | 低 | strip 函数严格匹配 `<attachments count=` 等系统生成特征 |
| feature flag 切换不彻底 | 低 | 低 | 一键回滚 + 灰度阶段任何指标退化立即停 |
| 跨轮 AI 找不到旧文件 | 中 | 中 | file_search/os.listdir 探索机制(已存在,无需新增)|
| DB 存储格式问题 | 无 | — | DB 不动,纯改 LLM 输入 |

---

## 6. 关键设计决策

### 6.1 为什么 attachments 改 system 而不是 content block?

content block(Claude API 风格)需要修改我们与 LLM provider 的协议层,改动巨大。
system message 是兼容 OpenAI 协议的等价手段(语义角色分明),改动极小。

### 6.2 为什么剥离历史而不是干脆不存?

DB 存完整 content 有两个价值:
1. **审计/导出**:用户回看历史时能看到当时的附件元数据
2. **向后兼容**:不需要数据迁移,旧记录自然支持

只在「发给 LLM」时剥离,既净化又保留。

### 6.3 为什么不动 attachments 渲染逻辑?

`_format_attachments` 渲染的 XML 内容是合理的(name/type/status/path 都需要)。
异味来源不是 XML 内容,而是**它出现在 user message 里**这个位置。

只改位置,不改内容。

### 6.4 为什么这个改动跟路径协议优化独立?

| 关注点 | 这个改动 | 路径协议改动 |
|---|---|---|
| 影响层 | LLM 输入结构 | 沙盒执行 + 提示词 |
| 验证方式 | token + 对话质量 | 沙盒实测 + 文件链路 e2e |
| 风险面 | LLM 是否仍理解附件 | AI 写代码是否成功 |
| 回滚成本 | 一行 feature flag | 大量代码改动 |

混在一起做 → 出问题分不清 + 测试矩阵爆炸 + 灰度耦合。

---

## 7. 与路径协议项目的关系

| 维度 | 关系 |
|---|---|
| **独立性** | 完全独立,可并行/串行任意顺序 |
| **依赖** | 无 |
| **冲突** | 无(改的是 messages 结构,不动沙盒/提示词) |
| **协同** | 路径协议会在 attachments XML 中新增 `<path>` 字段,本项目改动不阻塞 |
| **推荐顺序** | **先做本项目**(改动小、风险低、快速见效)→ 再做路径协议(基础更干净) |

---

## 8. 下一步

1. **用户审本文档**
2. 审过后:
   - Phase 1 实现(1 天)
   - Phase 2 单元测试(0.5 天)
   - Phase 3 真实数据验证(1-2 天)
   - Phase 4 灰度上线(2-3 天)
3. 完成后:回到「文件路径协议统一」继续 13 模块测试

---

**版本**:V1.0(2026-06-04)
**关系文档**:[TECH_文件路径协议统一_MVP方案.md](TECH_文件路径协议统一_MVP方案.md)(并列,独立)
**审阅人**:_____(用户)
