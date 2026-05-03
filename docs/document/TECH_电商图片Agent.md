# TECH_电商图片Agent 技术方案

> 版本：v4.5 | 日期：2026-05-03

## 一、需求概述

新增**电商图模式**，在现有模式选择器中新增入口，与现有图生图/文生图/视频模式并列、互不冲突。

**核心体验**：
1. 用户在模式选择器中切换到"电商图模式"
2. 上传商品图 + 写简短描述
3. 点击 [AI提示词] 按钮 → AI自动生成专业提示词 → 填入输入框
4. 用户可查看/编辑提示词 → 点击发送
5. 主Agent 拆分提示词，逐张调用 ImageAgent → 图片逐张出现

**技术选型**：
- 提示词增强模型：`qwen3-vl-plus`（DashScope，1元/百万tokens，创意强+能看图）
- 图片生成：KIE adapter（现有基础设施，国内直连）
- 主Agent：DeepSeek V4（现有，不变）
- 背景去除：rembg（本地）
- 多尺寸裁切：Pillow
- 存储：复用现有 OSS/CDN 链路

**与现有功能的关系**：
- 现有"图生图/文生图"模式 → 保持不变，继续走 ImageHandler + KIE
- 新增"电商图模式" → 独立入口，走 ImageAgent + 四层提示词 + 风格系统
- 两者共用同一套 KIE 生图能力和积分体系，互不冲突

---

## 二、架构设计

### 2.1 整体流程

```
用户切换到图片模式（图生图/文生图）
         ↓
输入框出现 [✨AI提示词] 按钮 + 模式化 placeholder + Tab补全/标签组
         ↓
用户上传商品图 + 写简短描述（如"淘宝白底主图"）
         ↓
点击 [✨AI提示词]
         ↓
前端调 POST /api/image/enhance-prompt（轻量API，3-5秒）
  → GPT-5.4-Image-2 纯文本模式
  → 四层提示词框架（角色+品类+平台+风格）
  → 返回专业提示词 + 结构化 images[] + style_directive + 费用预估
         ↓
提示词填入输入框（图片附件保持不动，只更新文字）
费用预估显示在输入框下方
         ↓
用户查看/编辑提示词 → 点击发送（附带 image_task_meta 元数据）
         ↓
ChatHandler 接收消息：
  → 提取用户图片 URLs → executor._current_message_images
  → 启动工具循环
         ↓
主Agent 读取 image_task_meta，拆分为多次 image_agent 调用：
         ↓
第1次：image_agent(task="白底主图 800×800：商品居中...")
  → executor 三重自动注入：image_urls + style_directive + history_images
  → ImageAgent 单张生成 → 裁切 → 上传CDN
  → 成功：返回 AgentResult(collected_files=[{url, ...}])
  → 失败：返回 AgentResult(collected_files=[{failed:true, retry_context}])
  → 前端：占位符过渡为真实图片 / 显示失败占位符+重新生成按钮
         ↓
第2次：image_agent(task="场景图 800×800：...")
  → 同上流程
         ↓
全部完成，图片逐张出现（和 ChatGPT 体验一致）
```

### 2.2 模式选择器（新增"电商图模式"）

在现有模式选择器中新增一个入口，不改动已有模式：

```
┌─────────────────────────────────────┐
│ ✨ 智能          ✓                  │  ← 现有
│ 🖼️ 图生图模式                       │  ← 现有，不动
│ 📝 文生图模式                       │  ← 现有，不动
│ 🛒 电商图模式                       │  ← 新增！
│ 🎬 视频模式                        │  ← 现有，不动
└─────────────────────────────────────┘
```

```typescript
// InputArea.tsx — 扩展 SmartSubMode
type SmartSubMode = 'chat' | 'image-i2i' | 'image-t2i' | 'image-ecom' | 'video';
//                                                        ↑ 新增
```

| 模式 | 路径 | 特有功能 |
|-----|------|---------|
| **图生图**（现有） | ImageHandler → KIE | 基础生图 |
| **文生图**（现有） | ImageHandler → KIE | 基础生图 |
| **电商图**（新增） | 主Agent → ImageAgent → KIE | AI提示词 + 四层框架 + 风格系统 + 多尺寸 |

### 2.3 职责分工

| 角色 | 职责 | 不做的事 |
|-----|------|---------|
| **前端** | 模式切换、AI按钮、Tab补全、发送元数据、图片展示、失败重试 | 不做提示词构建、不做图片生成 |
| **enhance API** | 四层提示词组装、结构化拆分、风格持久化、费用预估 | 不做图片生成 |
| **主Agent** | 理解意图、按 image_task_meta 拆分调用、流式进度输出 | 不传 image_urls（自动注入） |
| **ImageAgent** | 单张图片生成、去背景、裁切、上传CDN | 不做拆分（主Agent拆）、不做风格管理（DB自动读） |
| **executor** | 三重自动注入（image_urls + style + history） | — |

### 2.4 与现有工具的分界

```
generate_image（通用图片生成）：
  - 非电商场景：画一只猫、生成logo、画概念图
  - 单张，无平台/品类/风格体系
  - 用户在普通聊天模式下要求画图时使用

image_agent（电商图片生成）：
  - 电商商品图片：主图、场景图、详情页、白底图
  - 有平台规范 + 品类模板 + 风格体系 + 全局风格一致性
  - 用户在图生图/文生图模式下发送时使用
```

---

## 三、前端交互设计

### 3.1 输入框变化

**普通聊天模式（不变）**：
```
┌──────────────────────────────────────────────┐
│ 发送消息....                                  │
│ ✨智能 ▾  ⚙️  (深度思考)  📁工作区   📎  ➤    │
└──────────────────────────────────────────────┘
```

**图片模式（切换后）**：
```
┌──────────────────────────────────────────────┐
│ ┌─────┐                                      │
│ │ 📷 ✕│  ← 上传的图片（图生图模式）            │
│ │  1  │                                      │
│ └─────┘                                      │
│ [✨AI提示词]  描述你想要的商品图片....          │  ← 新增按钮+引导文案
│ ✨智能 ▾  ⚙️  (深度思考)  📁工作区   📎  ➤    │
└──────────────────────────────────────────────┘
```

**点击 [AI提示词] 后**：
```
┌──────────────────────────────────────────────┐
│ ┌─────┐                                      │
│ │ 📷 ✕│  ← 图片保持不动 ✅                     │
│ │  1  │                                      │
│ └─────┘                                      │
│ [✨AI提示词]                                  │
│ 请为以下商品生成淘宝电商图片：                  │  ← 只有文字被AI替换
│ 1. 白底主图 800×800：商品居中，纯白背景...      │
│ 2. 场景图 800×800：浅色木桌...                 │
│                                              │
│ 预计生成 2 张图片，消耗约 16 积分              │  ← 费用预估
│ ✨智能 ▾  ⚙️  (深度思考)  📁工作区   📎  ➤    │
└──────────────────────────────────────────────┘
```

### 3.2 模式化 Placeholder

> **项目实际**：模式字段为 `smartSubMode`（定义在 `InputArea.tsx:83`），值为 `'chat' | 'image-i2i' | 'image-t2i' | 'video'`。

```typescript
// InputArea.tsx — 根据 smartSubMode 切换 placeholder
const getPlaceholder = (subMode: SmartSubMode) => {
  switch (subMode) {
    case "image-i2i":    // 图生图（项目实际字段名）
      return '描述你想要的图片效果，例如：\n'
           + '"淘宝白底主图 + 一张场景图，风格清新自然"\n'
           + '必填：平台(淘宝/京东/拼多多) + 图片类型(主图/场景/详情)';
    case "image-t2i":    // 文生图（项目实际字段名）
      return '描述商品和想要的图片效果，例如：\n'
           + '"一款红色皮质手提包，淘宝主图，高端奢华风格"\n'
           + '必填：商品描述 + 平台 + 图片类型';
    default:
      return "发送消息...";
  }
};
```

### 3.3 输入辅助

**桌面端：Tab 补全**

| 分类 | 触发词 → 补全结果 |
|-----|-----------------|
| **平台** | `淘`→`淘宝` / `京`→`京东` / `拼`→`拼多多` / `抖`→`抖音` / `小红`→`小红书` |
| **图片类型** | `白底`→`白底主图 800×800` / `场景`→`场景图 800×800` / `详情`→`详情页 750×宽` / `竖`→`竖图 750×1000` |
| **风格** | `极简`→`极简风格` / `网感`→`网感风格` / `种草`→`种草风格` / `奢华`→`高端奢华风格` / `清新`→`清新自然风格` / `国潮`→`国潮风格` / `复古`→`复古文艺风格` |

```typescript
const TAB_COMPLETIONS: Record<string, string> = {
  "淘": "淘宝", "京": "京东", "拼": "拼多多", "抖": "抖音", "小红": "小红书",
  "白底": "白底主图 800×800", "场景": "场景图 800×800",
  "详情": "详情页 750×宽", "竖": "竖图 750×1000",
  "极简": "极简风格", "网感": "网感风格", "种草": "种草风格",
  "奢华": "高端奢华风格", "清新": "清新自然风格",
  "国潮": "国潮风格", "复古": "复古文艺风格", "暖": "暖调生活风格",
};

const handleKeyDown = (e: KeyboardEvent) => {
  if (e.key !== "Tab" || (smartSubMode !== "image-i2i" && smartSubMode !== "image-t2i")) return;
  e.preventDefault();
  const cursorPos = inputRef.current?.selectionStart ?? inputText.length;
  const textBefore = inputText.slice(0, cursorPos);
  const sortedKeys = Object.keys(TAB_COMPLETIONS).sort((a, b) => b.length - a.length);
  for (const key of sortedKeys) {
    if (textBefore.endsWith(key)) {
      const completion = TAB_COMPLETIONS[key];
      const newText = textBefore.slice(0, -key.length) + completion + inputText.slice(cursorPos);
      setInputText(newText);
      const newPos = cursorPos - key.length + completion.length;
      requestAnimationFrame(() => inputRef.current?.setSelectionRange(newPos, newPos));
      return;
    }
  }
};
```

**移动端：可点击标签组**（替代 Tab 补全）

```
┌──────────────────────────────────────────────┐
│ 平台: [淘宝] [京东] [拼多多] [抖音] [小红书]   │  ← 可点击插入文字
│ 类型: [白底主图] [场景图] [竖图] [详情页]       │
│ 风格: [极简] [清新] [网感] [种草] [奢华] [国潮] │
├──────────────────────────────────────────────┤
│ [✨AI提示词]  输入框...                        │
└──────────────────────────────────────────────┘
```

```typescript
const isMobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
// 移动端显示标签组，桌面端用 Tab 补全
{(smartSubMode === "image-i2i" || smartSubMode === "image-t2i") && isMobile && (
  <QuickTagBar
    tags={[
      { group: "平台", items: ["淘宝", "京东", "拼多多", "抖音", "小红书"] },
      { group: "类型", items: ["白底主图", "场景图", "竖图", "详情页"] },
      { group: "风格", items: ["极简", "清新", "网感", "种草", "奢华", "国潮"] },
    ]}
    onSelect={(text) => insertAtCursor(text)}
  />
)}
```

### 3.4 AI提示词按钮 + 发送逻辑

> **项目实际**：`sendMessage` 接受 `params: Record<string, unknown>` 传递自定义元数据（`messageSender.ts:29-48`）。`image_task_meta` 通过 `params` 透传，不是独立字段。

```typescript
// InputArea.tsx

const showEnhanceButton = smartSubMode === "image-i2i" || smartSubMode === "image-t2i";
const [imageTaskMeta, setImageTaskMeta] = useState<ImageTask[] | null>(null);
const [costEstimate, setCostEstimate] = useState<CostEstimate | null>(null);

const handleEnhancePrompt = async () => {
  setIsEnhancing(true);  // 按钮进入 loading，防抖
  const result = await fetch("/api/image/enhance-prompt", {
    method: "POST",
    body: JSON.stringify({
      text: inputText,
      image_urls: uploadedImages.map(img => img.url),
      conversation_id: conversationId,
      platform: selectedPlatform,
      style: selectedStyle,
    }),
  });
  const data = await result.json();
  setInputText(data.enhanced_prompt);     // 只更新文字，图片不碰
  setImageTaskMeta(data.images);          // 保存结构化拆分元数据
  setCostEstimate(data.cost_estimate);    // 费用预估
  setIsEnhancing(false);
};

// 发送时通过 params 透传 image_task_meta（项目实际签名）
const handleSend = () => {
  sendMessage({
    conversationId,
    content: buildContentParts(inputText, uploadedImages),
    generationType: smartSubMode === "image-i2i" ? "image" : "image",
    model: currentModel,
    params: {
      image_task_meta: imageTaskMeta,    // 通过 params 透传到后端
    },
  });
};
```

### 3.5 前端改动文件清单

| 文件 | 改动 |
|-----|------|
| `ChatInput.tsx` | [AI提示词] 按钮(含防抖) + placeholder + Tab补全/标签组 + imageTaskMeta + 费用预估 + 图片未上传置灰 + 图片变更提示 |
| 消息发送逻辑 | 发送时附带 `image_task_meta` 元数据 |
| content block 渲染 | 处理 `ImagePart.failed === true` → 显示 `FailedMediaPlaceholder` |
| 重试逻辑 | `handleRetryImage` → 调 `/api/image/retry` + 原位更新 |

---

## 四、后端 API

### 4.1 POST /api/image/enhance-prompt（提示词增强）

```
Request:
{
  "text": "淘宝白底主图，再来一张场景图",
  "image_urls": ["https://cdn/.../商品图.jpg"],   // 可选
  "platform": "taobao",                          // 可选，默认 taobao
  "style": "fresh",                              // 可选，风格预设
  "conversation_id": "conv_123"                   // 必传，用于风格持久化
}

Response:
{
  "enhanced_prompt": "请为以下商品生成淘宝电商图片：\n1. 白底主图 800×800：...\n2. 场景图 800×800：...",
  "images": [
    {"index": 1, "type": "white_bg", "description": "白底主图 800×800：商品居中...", "aspect_ratio": "1:1"},
    {"index": 2, "type": "scene", "description": "场景图 800×800：浅色木桌...", "aspect_ratio": "1:1"}
  ],
  "style_directive": "配色：暖色调，主色 #F5E6D3...",
  "style_mode": "create",
  "category": "general",
  "platform": "taobao",
  "cost_estimate": {"estimated_credits": 16, "per_image_credits": 8, "image_count": 2}
}
```

#### 后端实现

```python
# backend/routes/image_routes.py

@router.post("/api/image/enhance-prompt")
async def enhance_prompt(req: EnhancePromptRequest, user=Depends(get_current_user), db=Depends(get_db)):
    builder = PromptBuilder()

    # 1. 品类检测（从文本关键词自动匹配）
    category = builder.detect_category(req.text)

    # 2. 平台检测（文本中的平台关键词覆盖默认值）
    platform = builder.detect_platform(req.text, req.platform or "taobao")

    # 3. 风格管理（三模式自动切换）
    existing_style = await db.fetchval(
        "SELECT image_style_directive FROM conversations WHERE id = $1",
        req.conversation_id
    )
    if not existing_style:
        style_mode = "create"
    elif _is_style_adjustment(req.text):
        style_mode = "update"
    else:
        style_mode = "reuse"

    # 4. 组装四层 system prompt
    user_style = req.style or builder.resolve_style_from_matrix(category, platform)
    system_prompt = builder.build_system_prompt(category, platform, user_style)
    if style_mode == "update":
        system_prompt += f"\n\n当前风格：\n{existing_style}\n\n用户要求调整，请在此基础上修改。"
    elif style_mode == "reuse":
        system_prompt += f"\n\n必须严格延续以下风格：\n{existing_style}"

    # 5. 构建 user message
    user_content = builder.build_enhance_prompt(req.text, bool(req.image_urls))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_multimodal_content(user_content, req.image_urls)},
    ]

    # 6. 调 qwen3-vl-flash（DashScope，能看图+写提示词）
    from services.adapters.dashscope_adapter import DashScopeChatAdapter
    adapter = DashScopeChatAdapter(
        api_key=settings.dashscope_api_key,
        model=settings.image_enhance_model,  # "qwen3-vl-plus"
        base_url=settings.dashscope_base_url,
        timeout=settings.image_enhance_timeout,
    )
    try:
        response = await adapter.chat_sync(messages=messages)
        images = _parse_image_tasks(response.content)

        # 7. 提取并持久化 style_directive
        if style_mode in ("create", "update"):
            new_style = _extract_style_directive(response.content)
            await db.execute(
                "UPDATE conversations SET image_style_directive = $1 WHERE id = $2",
                new_style, req.conversation_id
            )
        else:
            new_style = existing_style

        return {
            "enhanced_prompt": response.content,
            "images": images,
            "style_directive": new_style,
            "style_mode": style_mode,
            "category": category,
            "platform": platform,
            "cost_estimate": _estimate_credits(len(images)),
        }
    finally:
        await adapter.close()
```

#### 辅助函数

```python
def _parse_image_tasks(content: str) -> list[dict]:
    """从模型输出解析结构化图片任务（"1. xxx" "2. xxx" 格式）。"""
    import re
    tasks = []
    for match in re.finditer(r'(\d+)\.\s*(.+?)(?=\n\d+\.|$)', content, re.DOTALL):
        desc = match.group(2).strip()
        img_type = "main"
        if "白底" in desc: img_type = "white_bg"
        elif "场景" in desc: img_type = "scene"
        elif "详情" in desc or "卖点" in desc: img_type = "detail"
        aspect = "3:4" if ("750×1000" in desc or "3:4" in desc) else "1:1"
        tasks.append({"index": int(match.group(1)), "type": img_type, "description": desc, "aspect_ratio": aspect})
    # 解析失败兜底：整段当1张图
    if not tasks:
        tasks = [{"index": 1, "type": "main", "description": content.strip(), "aspect_ratio": "1:1"}]
    return tasks

def _extract_style_directive(content: str) -> str:
    """从模型输出中提取风格描述。取前200字作为风格摘要。"""
    # 模型输出中通常包含配色、光线、氛围等描述
    # 提取关键风格信息作为 style_directive
    lines = content.split("\n")
    style_lines = [l for l in lines if any(kw in l for kw in ["配色", "色调", "光线", "风格", "氛围"])]
    return "\n".join(style_lines[:5]) if style_lines else content[:200]

def _estimate_credits(image_count: int) -> dict:
    per_image = 8  # 单张约8积分（$0.04 × 200）
    return {"estimated_credits": per_image * image_count, "per_image_credits": per_image, "image_count": image_count}

def _build_multimodal_content(text: str, image_urls: list[str] | None) -> list[dict]:
    parts = [{"type": "text", "text": text}]
    for url in (image_urls or []):
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts
```

#### 提示词模板

```python
# prompt_builder.py

def build_enhance_prompt(self, text: str, has_images: bool) -> str:
    parts = [f"用户需求：{text}"]
    if has_images:
        parts.append("用户已上传商品图片，请分析图片中的商品特征。")
    parts.append("""请生成专业的电商图片提示词。

要求：
1. 按5要素（主体、背景、光线、角度、风格）组织每张图描述
2. 明确标注类型和尺寸
3. 中文自然语言
4. 未指定数量时默认2-3张（白底主图+场景图）
5. **必须用 "数字." 有序列表格式**
6. 每条格式：`序号. 图片类型 尺寸：详细描述`

输出格式（严格遵循）：
请为以下商品生成淘宝电商图片：
1. 白底主图 800×800：商品居中，占画面70%，纯白背景，柔光箱45度布光，自然底部投影
2. 场景图 800×800：商品放在浅色木桌上，自然窗光从左侧照入，暖色调，搭配绿植和咖啡杯
平台：淘宝 | 共2张""")
    return "\n\n".join(parts)
```

### 4.2 POST /api/image/retry（单张重试）

```
Request:
{
  "conversation_id": "conv_123",
  "message_id": "msg_456",
  "task": "场景图 800×800：浅色木桌...",        // 复用原提示词
  "image_urls": ["cdn/.../商品图.jpg"],         // 复用原图片
  "platform": "taobao",
  "style_directive": "配色暖色调...",            // 复用原风格
  "part_index": 1                               // 消息中第几张图
}

Response:
{"success": true, "image_url": "https://cdn/.../new_scene.png"}
或
{"success": false, "error": "图片生成服务繁忙"}
```

```python
@router.post("/api/image/retry")
async def retry_image(req: RetryImageRequest, user=Depends(get_current_user), db=Depends(get_db)):
    """单张图片原位重新生成。"""
    agent = ImageAgent(db=db, user_id=user.id, conversation_id=req.conversation_id)
    result = await agent.execute(
        task=req.task, image_urls=req.image_urls,
        platform=req.platform, style_directive=req.style_directive,
    )
    if result.status == "success" and result.collected_files:
        # 原位替换消息中对应位置的 ImagePart
        await _update_message_image_part(db, req.message_id, req.part_index, result.collected_files[0])
        return {"success": True, "image_url": result.collected_files[0]["url"]}
    return {"success": False, "error": result.summary}

async def _update_message_image_part(db, message_id: str, part_index: int, new_part: dict):
    row = await db.fetchrow("SELECT content FROM messages WHERE id = $1", message_id)
    content = row["content"] or []
    img_count = 0
    for i, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "image":
            if img_count == part_index:
                content[i] = new_part
                break
            img_count += 1
    await db.execute("UPDATE messages SET content = $1 WHERE id = $2", json.dumps(content), message_id)
```

---

## 五、四层提示词系统

### 5.1 架构总览

```
┌─────────────────────────────────────────────┐
│ 第1层：角色 + 通用规则（固定注入 system）      │
│   专业电商摄影师身份 + 5要素公式 + 质量红线    │
├─────────────────────────────────────────────┤
│ 第2层：品类模板（按商品自动匹配，共12个）      │
│   服装/食品/电子/美妆/家居/珠宝/母婴          │
│   + 运动/宠物/家电/箱包/农产品                │
├─────────────────────────────────────────────┤
│ 第3层：平台规范 + 风格趋势（共6个平台）        │
│   淘宝/天猫/京东/拼多多/抖音/小红书           │
├─────────────────────────────────────────────┤
│ 第4层：风格预设（用户可选或矩阵自动推荐）      │
│   极简/暖调/奢华/清新/网感/种草/复古/国潮      │
└─────────────────────────────────────────────┘
         ↓ 四层叠加 + 品类×平台矩阵
   完整 system prompt → enhance API / ImageAgent 共用
```

### 5.2 第1层：角色 + 通用规则

```python
SYSTEM_PROMPT_BASE = """你是专业的电商产品摄影师兼美术指导。精通中英文摄影术语，
拥有丰富的商业产品摄影经验。你的任务是根据商品信息和用户需求，
生成高转化率的电商产品图片描述。

## 提示词构建5要素（每张图必须包含，按此顺序）
1. **主体** — 商品是什么、材质、颜色、关键特征
2. **背景** — 纯白/渐变/大理石/木桌/场景环境
3. **光线** — 柔光箱/自然窗光/逆光/环形灯，方向和强度
4. **角度** — 俯拍/平拍/45度/微距/三分之四视角
5. **风格** — 商业摄影/生活方式/极简/高端/清新

## 质量红线
- 商品准确性永远优先于艺术效果
- 商品必须是画面焦点，清晰可辨
- 色彩必须准确还原商品真实颜色
- 不添加水印/文字/Logo（除非用户要求）
- 缩略图尺寸下细节仍需可辨（移动端优先）

## 参考图处理原则
当用户提供参考图时：
- 提取风格元素（色调、光线、构图、氛围）
- 将风格应用到商品图上，商品本身的外观不可被风格改变
- 明确区分"风格参考"和"商品素材"
"""
```

### 5.3 第2层：品类模板（12个）

```python
CATEGORY_TEMPLATES = {
    "clothing": {
        "label": "服装", "keywords": ["衣服","裙","裤","外套","T恤","卫衣","衬衫","连衣裙","西装","羽绒服","内衣","袜","帽"],
        "prompt_guide": "角度：正面平拍/悬挂拍/平铺拍 | 光线：柔和漫射，侧光展示质感 | 重点：面料纹理、版型剪裁 | 背景：白底/街拍场景",
    },
    "food": {
        "label": "食品饮料", "keywords": ["食品","零食","茶","咖啡","酒","水果","坚果","饮料","糕点","蛋糕","巧克力","奶","蜂蜜","调味","酱"],
        "prompt_guide": "角度：俯拍90度/45度/平视 | 光线：侧逆光，暖色调 | 重点：色泽鲜亮、质感诱人 | 背景：木桌/大理石/亚麻布 | 道具：食材散落、餐具",
    },
    "electronics": {
        "label": "电子产品", "keywords": ["手机","电脑","耳机","音箱","充电","数据线","键盘","鼠标","平板","相机","手表","智能","数码"],
        "prompt_guide": "角度：三分之四视角/俯拍/正面 | 光线：干净中性光，边缘光勾勒轮廓 | 重点：工艺细节、接口清晰 | 背景：深色渐变/纯白",
    },
    "cosmetics": {
        "label": "美妆护肤", "keywords": ["口红","面霜","精华","粉底","眼影","腮红","护肤","化妆","美妆","防晒","面膜","洗面奶","香水","卸妆"],
        "prompt_guide": "角度：30度微侧/正面/微距质地 | 光线：双45度柔光箱+轮廓光 | 重点：标签清晰、瓶身质感 | 背景：柔和渐变/大理石 | 道具：花瓣、水珠",
    },
    "furniture": {
        "label": "家居家具", "keywords": ["沙发","桌","椅","柜","床","灯","窗帘","地毯","书架","置物","花瓶","抱枕","挂画","收纳"],
        "prompt_guide": "角度：平视/俯拍/场景全景 | 光线：模拟自然日光 | 重点：材质纹理、尺寸比例 | 背景：完整房间场景 | 道具：书籍、咖啡杯、绿植",
    },
    "jewelry": {
        "label": "珠宝配饰", "keywords": ["项链","戒指","耳环","手镯","手链","珠宝","银饰","黄金","钻石","翡翠","玉","胸针","发饰","腰带"],
        "prompt_guide": "角度：微距特写/45度/佩戴展示 | 光线：柔光罩全方位漫射 | 重点：工艺细节、宝石光泽 | 背景：深色丝绒/白底",
    },
    "mother_baby": {
        "label": "母婴玩具", "keywords": ["奶瓶","玩具","积木","绘本","婴儿","儿童","母婴","尿不湿","奶粉","推车","安全座椅","童装"],
        "prompt_guide": "角度：平视(儿童视角)/45度/使用场景 | 光线：明亮温暖柔和 | 重点：色彩鲜艳、安全性 | 背景：明亮纯色/儿童房",
    },
    "sports": {
        "label": "运动户外", "keywords": ["运动","健身","跑步","瑜伽","球","户外","登山","骑行","泳","帐篷","鞋","运动服"],
        "prompt_guide": "角度：使用场景/45度/平铺 | 光线：自然户外光 | 重点：动态感、功能性展示 | 背景：运动场景/纯白",
    },
    "pets": {
        "label": "宠物用品", "keywords": ["宠物","猫","狗","猫粮","狗粮","猫砂","牵引","宠物玩具","猫窝","狗窝","宠物服"],
        "prompt_guide": "角度：与宠物互动/45度/平视 | 光线：温暖柔和 | 重点：宠物使用效果 | 背景：家庭环境/纯白",
    },
    "appliance": {
        "label": "家用电器", "keywords": ["冰箱","洗衣机","空调","微波炉","烤箱","吸尘器","电饭煲","热水器","净水","风扇","加湿","电器"],
        "prompt_guide": "角度：三分之四视角/使用场景 | 光线：明亮中性光 | 重点：使用场景融入家庭 | 背景：家庭场景/纯白",
    },
    "bags": {
        "label": "箱包", "keywords": ["包","背包","手提包","钱包","行李箱","书包","腰包","胸包","挎包","公文包","旅行箱"],
        "prompt_guide": "角度：正面/45度/打开状态 | 光线：柔光展示皮质纹理 | 重点：材质细节、容量展示 | 背景：简约场景/纯白",
    },
    "agriculture": {
        "label": "农产品生鲜", "keywords": ["水果","蔬菜","大米","茶叶","蜂蜜","干货","海鲜","肉","蛋","特产","有机","农产品","生鲜"],
        "prompt_guide": "角度：俯拍/45度/微距 | 光线：自然侧光，暖色调 | 重点：色泽饱满、新鲜感 | 背景：竹编/麻布/产地场景",
    },
}
DEFAULT_CATEGORY_TEMPLATE = "角度：45度/正面平拍 | 光线：双侧柔光箱 | 重点：特征清晰、色彩准确 | 背景：纯白/浅灰渐变"
```

### 5.4 第3层：平台规范 + 风格趋势（6个）

```python
PLATFORM_PROMPTS = {
    "taobao": "**规格**：主图800×800，竖图750×1000，第5张纯白底 | **趋势**：明亮干净，信任感强",
    "tmall":  "**规格**：同淘宝，品质感更高 | **趋势**：品牌调性统一，高端品类追求质感",
    "jd":     "**规格**：首图纯白底居中 | **趋势**：品质感和专业度最高，偏理性消费",
    "pdd":    "**规格**：白底优先，商品占70%+ | **趋势**：传统品类性价比直给；文创/年轻品类网感风格（强对比有梗）；日用/美妆小红书种草风",
    "douyin": "**规格**：竖图3:4效果好 | **趋势**：即时感真实感，年轻化情绪化，素人感>精修感",
    "xiaohongshu": "**规格**：1:1或3:4 | **趋势**：美感+生活方式，精致真实种草氛围，场景化>棚拍",
}
```

> 完整版平台规范（含详细技术要求+风格趋势）见 `prompts.py` 源码。上表为摘要。

### 5.5 第4层：风格预设（8个）

```python
STYLE_PRESETS = {
    "minimal":           "极简 — 大面积留白，纯色背景，元素极少，苹果/MUJI风",
    "warm_life":         "暖调生活 — 暖色调(米/木/奶油)，自然窗光，生活化道具",
    "luxury":            "高端奢华 — 深色背景，金属点缀，精致打光，强对比",
    "fresh":             "清新自然 — 浅色系，植物/花瓣/水珠，通透明亮",
    "internet_feel":     "网感 — 强视觉冲击，对比色鲜明，有梗有趣味，缩略图抢眼",
    "xiaohongshu_style": "种草风 — 精致不过度，莫兰迪/奶茶色系，场景化第一人称",
    "vintage":           "复古文艺 — 胶片感做旧色调，旧书/牛皮纸/干花/蜡封",
    "guochao":           "国潮 — 中国传统+现代设计，朱红/墨黑/金/靛蓝，书法/印章/纹样",
}
```

> 完整版风格预设（含详细 prompt_guide）见 `prompts.py` 源码。上表为摘要。

### 5.6 品类×平台风格矩阵

当用户未指定风格时，根据品类+平台自动推荐：

```python
CATEGORY_PLATFORM_STYLE = {
    "clothing":    {"pdd": "xiaohongshu_style", "xiaohongshu": "xiaohongshu_style", "douyin": "warm_life"},
    "food":        {"taobao": "warm_life", "pdd": "internet_feel", "xiaohongshu": "xiaohongshu_style", "douyin": "internet_feel"},
    "electronics": {"taobao": "minimal", "xiaohongshu": "minimal", "douyin": "minimal"},
    "cosmetics":   {"taobao": "fresh", "pdd": "xiaohongshu_style", "xiaohongshu": "xiaohongshu_style", "douyin": "xiaohongshu_style"},
    "furniture":   {"taobao": "warm_life", "pdd": "warm_life", "xiaohongshu": "warm_life", "douyin": "warm_life"},
    "jewelry":     {"taobao": "luxury", "pdd": "luxury", "xiaohongshu": "luxury", "douyin": "luxury"},
}

def _resolve_style(category, platform, user_style):
    if user_style: return user_style
    return CATEGORY_PLATFORM_STYLE.get(category, {}).get(platform)
```

**效果**：文创投拼多多→网感 / 文创投小红书→种草风 / 美妆投拼多多→种草风 / 珠宝投任何平台→奢华

### 5.7 摄影术语字典

```python
# photography_terms.py — ImageAgent 内部提升提示词专业度
LIGHTING_TERMS = {"柔光": "soft diffused light", "自然光": "natural window light", "逆光": "rim lighting, backlit", ...}
ANGLE_TERMS = {"俯拍": "overhead shot, flat lay", "平拍": "eye-level shot", "45度": "three-quarter view", ...}
COMPOSITION_TERMS = {"居中": "centered composition", "留白": "negative space", "浅景深": "shallow depth of field", ...}
BACKGROUND_TERMS = {"纯白": "pure white seamless", "大理石": "marble surface", "木桌": "rustic wooden table", ...}
```

### 5.8 多图片角色约定

```python
MULTI_IMAGE_GUIDE = """
用户上传了多张图片：
- 第1张：商品主体图（用于生成图片的主体）
- 第2张及之后：风格参考图（提取风格/氛围/构图参考，不提取商品）
请分析参考图的风格元素，将风格应用到商品主体图上，但商品外观必须保持准确。
"""
```

---

## 六、全局风格一致性机制

同一会话中生成的所有图片（主图、详情页）必须风格统一。

### 6.1 存储

```sql
ALTER TABLE conversations ADD COLUMN image_style_directive text;
```

### 6.2 三模式自动切换

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| **create** | DB 无值（首次） | 模型分析商品 → 生成风格 → 写 DB |
| **reuse** | DB 有值 + 非调整请求 | 注入已有风格 → 延续 |
| **update** | DB 有值 + 检测到"暖一点/换风格" | 基于旧风格+调整 → 更新 DB |

### 6.3 风格调整检测（含误判防护）

```python
def _is_style_adjustment(text: str) -> bool:
    # 第1层：肯定句式排除（"这个风格很好" 不是调整）
    satisfaction = ["很好","不错","可以","满意","喜欢","就这个","挺好","好的","OK","保持","继续","一样的"]
    if any(p in text for p in satisfaction):
        return False
    # 第2层：调整关键词
    adjust = ["换个风格","暖一点","冷一点","亮一点","暗一点","更高级","更简约","更活泼",
              "颜色换","色调调整","换成国潮","换成极简","不太满意","差点意思","调整一下"]
    return any(kw in text for kw in adjust)
```

### 6.4 可靠性保障

| 保障 | 机制 |
|------|------|
| **持久化** | style_directive 存 conversations 表，不依赖 Redis/前端/上下文 |
| **自动读取** | executor 从 DB 读，LLM 不需要传 |
| **智能复用** | enhance API 三模式自动切换 |

---

## 七、ImageAgent 核心设计

### 7.1 定位：单张图片生成器

每次调用只生成1张图片。多张由主Agent拆分后多次调用。与 ChatGPT + DALL-E 模式一致。

### 7.2 核心类

```python
class ImageAgent:
    """电商图片生成 Agent — 单张图片生成器。"""

    def __init__(self, db, user_id, conversation_id, org_id=None, task_id=None, message_id=None):
        self.db, self.user_id = db, user_id
        self.conversation_id, self.org_id = conversation_id, org_id
        self.task_id, self.message_id = task_id, message_id

    async def execute(self, task: str, **kwargs) -> AgentResult:
        image_urls = kwargs.get("image_urls", [])
        platform = kwargs.get("platform", "taobao")
        style_directive = kwargs.get("style_directive", "")

        # 1. 校验
        err = self._validate_input(task, **kwargs)
        if err:
            return AgentResult(status="error", summary=err, source="image_agent")

        # 2. 锁积分
        # 注意：现有 CreditMixin._lock_credits 签名为 (task_id, user_id, amount, reason, org_id)
        # 无 TTL 参数。超时退还需扩展 CreditMixin 或用定时任务兜底（见 §13.7）
        tx_id = self._lock_credits(
            task_id=f"img_{uuid4().hex[:12]}", user_id=self.user_id,
            amount=credits_needed, reason=f"Image: {task[:30]}",
            org_id=self.org_id,
        )

        try:
            # 3. 构建最终提示词（注入全局风格约束）
            final_prompt = self._build_final_prompt(task, style_directive)

            # 4. 预处理（白底图去背景，失败降级用原图）
            ref_images = image_urls
            if "白底" in task and image_urls:
                try:
                    ref_images = [await ImageProcessor.remove_background(image_urls[0])]
                except Exception:
                    ref_images = image_urls  # 降级

            # 5. 调 KIE adapter 生成图片（复用现有 create_image_adapter）
            from services.adapters.factory import create_image_adapter
            model_id = settings.image_agent_kie_i2i_model if ref_images else settings.image_agent_kie_model
            adapter = create_image_adapter(model_id)
            result = await adapter.generate(
                prompt=final_prompt,
                image_urls=ref_images if ref_images else None,
                size=self._detect_aspect_ratio(task),
                wait_for_result=True, max_wait_time=90.0, poll_interval=2.0,
            )
            raw_image = result.image_urls[0] if result.image_urls else None
            if not raw_image:
                raise ImageGenerationError(result.fail_msg or "图片生成失败")
            await adapter.close()

            # 6. Pillow 校验 + 裁切
            resized = ImageProcessor.resize_for_platform(raw_image, platform)

            # 7. 上传 CDN（失败重试1次）
            cdn_url = await self._upload_with_retry(resized)

            # 8. 确认扣费
            self._confirm_deduct(tx_id)

            return AgentResult(
                status="success", summary=f"已生成图片：{task[:30]}",
                source="image_agent",
                collected_files=[{
                    "type": "image", "url": cdn_url,
                    "width": resized.width, "height": resized.height,
                    "alt": task[:50],
                }],
                metadata={"platform": platform, "model": model_id},
            )

        except Exception as e:
            self._refund_credits(tx_id)
            # 失败：返回正确尺寸的 failed ImagePart + retry_context
            fail_w, fail_h = self._detect_dimensions(task, platform)
            return AgentResult(
                status="error", summary=f"图片生成失败：{e}", source="image_agent",
                collected_files=[{
                    "type": "image", "url": None,
                    "width": fail_w, "height": fail_h,  # ← 从task解析，不写死
                    "alt": task[:50], "failed": True, "error": str(e),
                    "retry_context": {
                        "task": task, "image_urls": image_urls,
                        "platform": platform, "style_directive": style_directive,
                    },
                }],
            )

    def _build_final_prompt(self, task: str, style_directive: str) -> str:
        if not style_directive:
            return task
        return f"【全局风格约束 — 必须严格遵循】\n{style_directive}\n\n【图片生成任务】\n{task}"

    def _validate_input(self, task, **kwargs):
        if not task or len(task) > 2000:
            return "提示词为空或过长（限2000字）"
        allowed = {"cdn.everydayai.com.cn", "img.everydayai.com.cn"}
        for url in kwargs.get("image_urls", []):
            from urllib.parse import urlparse
            if (urlparse(url).hostname or "") not in allowed:
                return "不支持的图片来源"
        return None
```

### 7.3 图片生成：复用 KIE adapter

不新建 ImageGenerator 类。直接复用现有 `create_image_adapter` + `KieImageAdapter`（已在 §7.2 核心类中集成）。

KIE adapter 已有的能力（直接复用，不重写）：
- 异步任务创建 + 同步等待轮询（`wait_for_result=True`）
- 多模型支持（gpt-image-2 文生图/图生图、nano-banana-pro）
- 积分预估 `estimate_cost()`
- 回调解析 `parse_callback()`
- 智能重试（Smart Mode 下自动切换备选模型，ImageHandler 已实现）
```

### 7.4 Thinking 进度推送

```python
async def _push_thinking(self, text: str) -> None:
    if not self.task_id or not self.message_id: return
    try:
        from schemas.websocket_builders import build_thinking_chunk
        from services.websocket_manager import ws_manager
        msg = build_thinking_chunk(
            task_id=self.task_id, conversation_id=self.conversation_id,
            message_id=self.message_id, chunk=f"\n── Image Agent ──\n→ {text}\n",
        )
        await ws_manager.send_to_task_or_user(self.task_id, self.user_id, msg)
    except Exception: pass
```

主Agent多次调用ImageAgent时，自然输出进度文字（"正在生成白底主图..."），用户通过流式输出看到进度。ImageAgent内部`_push_thinking`可补充更细粒度的进度。

---

## 八、自动注入与上下文管理

### 8.1 executor 三重自动注入

所有关键上下文由 executor 自动注入，**LLM 只传 task 参数**：

```python
# tool_executor.py

async def _image_agent(self, args):
    # 注入1：用户上传的图片（从当前消息提取）
    if not args.get("image_urls"):
        args["image_urls"] = getattr(self, "_current_message_images", [])

    # 注入2：全局风格（从 DB 读取）
    if not args.get("style_directive"):
        style = await self.db.fetchval(
            "SELECT image_style_directive FROM conversations WHERE id = $1",
            self.conversation_id)
        if style: args["style_directive"] = style

    # 注入3：历史生成图片（从消息 FilePart 查，供修改引用）
    if not args.get("history_images"):
        args["history_images"] = await self._get_conversation_image_parts(self.conversation_id)

    agent = ImageAgent(
        db=self.db, user_id=self.user_id, conversation_id=self.conversation_id,
        org_id=self.org_id, task_id=getattr(self, "_task_id", None),
        message_id=getattr(self, "_message_id", None),
    )
    return await agent.execute(**args)

async def _get_conversation_image_parts(self, conversation_id):
    rows = await self.db.fetch("""
        SELECT content FROM messages WHERE conversation_id = $1 AND role = 'assistant'
        ORDER BY created_at DESC LIMIT 20
    """, conversation_id)
    images = []
    for row in rows:
        for part in (row["content"] or []):
            if isinstance(part, dict) and part.get("type") == "file":
                if (part.get("mime_type") or "").startswith("image/"):
                    images.append({"url": part["url"], "name": part["name"]})
    return images
```

### 8.2 ChatHandler 注入图片上下文

```python
# chat_tool_mixin.py — 创建 executor 时
executor = ToolExecutor(db, user_id, conversation_id, org_id, request_ctx)
executor._current_message_images = [
    part["image_url"]["url"] for part in user_message.get("content", [])
    if isinstance(part, dict) and part.get("type") == "image_url"
]
```

### 8.3 混合对话上下文（[FILE] + placeholder）

图片生成结果通过现有 `[FILE]` 标签机制处理，**URL 不进 LLM 上下文**：

```
ImageAgent → collected_files → ChatToolMixin._extract_file_parts()
  → FilePart 存入 message.content（DB，供历史引用）
  → placeholder 替换："🖼️ 白底主图 800×800 已生成（将自动展示给用户）"
  → LLM 上下文只有 placeholder（≈30 tokens/张，不含 URL/base64）
  → 前端通过 content_block_add 独立通道展示真实图片
```

**placeholder 格式需改为描述性**（`🖼️ {name} 已生成`），供后续引用时 LLM 理解"场景图"指哪张。

> **项目实际 gap**：现有 `_extract_file_parts`（chat_tool_mixin.py:492）对图片固定返回 `📊 图表已生成...`。需改为根据 `name` 动态生成：
> ```python
> if mime_type.startswith("image/"):
>     return f"🖼️ {name} 已生成（将自动展示给用户，不要重复描述）"
> ```

### 8.4 历史图片修改引用

用户说"把场景图背景换成木桌" → LLM 理解 → 调 image_agent → executor 自动注入 history_images → ImageAgent 模糊匹配：

```python
def _find_reference_image(self, task, history_images):
    for kw in ["场景", "白底", "竖图", "主图", "详情", "卖点"]:
        if kw in task:
            for img in history_images:
                if kw in img.get("name", ""): return img["url"]
    return history_images[0]["url"] if history_images else None
```

### 8.5 可靠性保障总览

| 环节 | 机制 | 依赖 LLM？ |
|------|------|:---:|
| 提示词拆分 | enhance API 返回 images[] | ❌ |
| 用户图片传递 | executor 注入 _current_message_images | ❌ |
| 风格一致性 | style_directive 存 DB + executor 注入 | ❌ |
| 历史图片引用 | FilePart 消息历史 + executor 注入 | ❌ |
| 上下文不污染 | [FILE] → placeholder | ❌ |
| 积分安全 | lock/confirm/refund + TTL 超时退还 | ❌ |

---

## 九、失败处理与原位重试

### 9.1 问题：现有 failedMediaType 不覆盖 ImageAgent

ImageAgent 作为 chat 工具调用时 `generation_params.type = "chat"`，现有失败占位符逻辑（依赖 `type !== "chat"`）不触发。

### 9.2 方案：failed ImagePart + retry_context

ImageAgent 失败时返回 `collected_files=[{failed:true, retry_context:{task, image_urls, platform, style_directive}}]`。

- 前端 content block 渲染时检测 `failed === true` → 显示 `FailedMediaPlaceholder`（裂开图标 + hover "重新生成"按钮）
- 点击"重新生成" → 调 `POST /api/image/retry`（携带完整 retry_context）→ 原位替换
- 再次失败 → 恢复裂开占位符

### 9.3 前端原位重试

```typescript
const handleRetryImage = async (failedPart: ImagePart) => {
  const { task, image_urls, platform, style_directive } = failedPart.retry_context!;
  updateImagePartStatus(failedPart, { failed: false, url: null }); // 切为脉冲动画
  try {
    const res = await fetch("/api/image/retry", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId, message_id: message.id,
        task, image_urls, platform, style_directive, part_index: partIndex,
      }),
    });
    const data = await res.json();
    if (data.success) {
      updateImagePartStatus(failedPart, { failed: false, url: data.image_url, retry_context: undefined });
    } else {
      updateImagePartStatus(failedPart, { failed: true, error: data.error });
      toast.error("重新生成失败");
    }
  } catch { updateImagePartStatus(failedPart, { failed: true }); }
};
```

### 9.4 多图部分失败效果

```
第1次成功：[白底主图 ✅]
第2次失败：[🖼️(裂开) + 重新生成按钮]  ← 点击原位重试
第3次成功：[竖图 ✅]
```

### 9.5 所有失败场景覆盖

| 失败场景 | 后端行为 | 前端效果 |
|---------|---------|---------|
| 模型拒绝/超时/429/图片损坏/CDN上传失败 | `collected_files=[{failed:true, retry_context}]` | 裂开占位符 + 原位重试 |
| 积分不足 | `AgentResult(error)` 无 collected_files | 文字提示 |
| rembg 去背景失败 | 降级用原图继续 | 正常显示 |

---

## 十、前端展示：多图布局 + 占位符 + 全链路复用

### 10.1 多图网格布局：按尺寸分组，不混排

不同尺寸的图片不放在同一网格（高度不齐，观感差）。按 `aspect_ratio` 分组，每组独立网格。这是行业标准做法（ChatGPT 同尺寸网格 / Canva 按尺寸分组 / 微信裁切为统一正方形）。

```
用户生成5张图（3张 1:1 + 2张 3:4）→ 按尺寸分组：

  ┌──────┐ ┌──────┐ ┌──────┐
  │ 白底  │ │ 场景  │ │ 场景2 │   ← 3张 1:1 → 一行网格
  │800×800│ │800×800│ │800×800│
  └──────┘ └──────┘ └──────┘
  ┌────┐ ┌────┐
  │竖图 │ │竖图2│                  ← 2张 3:4 → 单独一行
  │750× │ │750× │
  │1000 │ │1000 │
  └────┘ └────┘
```

**前端分组逻辑**：

```typescript
function groupImagesByRatio(images: ImagePart[]): ImagePart[][] {
  const groups: Map<string, ImagePart[]> = new Map();
  for (const img of images) {
    const ratio = img.width && img.height ? `${img.width}:${img.height}` : "1:1";
    if (!groups.has(ratio)) groups.set(ratio, []);
    groups.get(ratio)!.push(img);
  }
  return [...groups.values()];
}

// 渲染：每组一个 AiImageGrid
{groupImagesByRatio(imagePartsInMessage).map((group, i) => (
  <AiImageGrid key={i} imageUrls={group.map(g => g.url)} />
))}
```

**AiImageGrid 内部已有网格能力**（CSS `repeat(auto-fill, ...)`）：同组内 2张→2列、3张→3列、4张→2×2、5张→3+2 自动换行，不超出气泡宽度。

### 10.2 占位符：逐张出现，按宽高比适配

占位符跟随主Agent调用节奏**逐个出现**，不一次性渲染所有。用 CSS `aspect-ratio` 控制宽高比，宽度自适应气泡，不按像素渲染：

```
主Agent："正在生成白底主图..."
  → 占位符①出现（aspect-ratio: 1/1）
  → 15秒后过渡为真实图片 ✅
  → use-stick-to-bottom 自动滚动

主Agent："正在生成竖图..."
  → 占位符②出现（aspect-ratio: 3/4，更高）
  → 过渡为竖图 ✅
```

```typescript
function getAspectRatio(ratio: string): number {
  const map: Record<string, number> = { "1:1": 1, "3:4": 3/4, "4:3": 4/3, "16:9": 16/9 };
  return map[ratio] || 1;
}

// 占位符：宽度受气泡约束，高度按比例自适应
<div style={{ aspectRatio: getAspectRatio(meta.aspect_ratio), maxWidth: '280px' }}>
  <MediaPlaceholder />
</div>
```

**三场景尺寸来源**：

| 场景 | 尺寸来源 | 用途 |
|------|---------|------|
| 生成中占位符 | `image_task_meta[i].aspect_ratio` → CSS aspect-ratio | 占位符宽高比 |
| 成功图片 | `collected_files[i].width/height` | 实际渲染尺寸 |
| 失败占位符 | ImageAgent `_detect_dimensions(task, platform)` | 裂开占位符宽高比 |

**失败占位符尺寸（后端）** — 不写死 800×800，从 task 解析：

```python
def _detect_dimensions(self, task: str, platform: str) -> tuple[int, int]:
    if "750×1000" in task or "3:4" in task or "竖图" in task:
        return (750, 1000)
    if "480×480" in task:
        return (480, 480)
    return PLATFORM_SIZES.get(platform, {}).get("main", [(800, 800)])[0]
```

### 10.3 复用现有组件

| 组件 | 功能 | 改动 |
|------|------|:---:|
| `MediaPlaceholder` | 生成中脉冲动画 | 无 |
| `AiGeneratedImage` | 单图展示+懒加载+重试 | 无 |
| `AiImageGrid` | 多图网格+重新生成 | 无 |
| `ImagePreviewModal` | 全屏预览+缩放(0.5x-4x)+拖拽 | 无 |
| `ImageContextMenu` | 右键引用/复制/下载 | 无 |
| `FailedMediaPlaceholder` | 裂开图标+重新生成按钮 | 无 |

**前端改动**：
- 同消息内 ImagePart 按 aspect_ratio 分组 → 每组传 `AiImageGrid`
- content block 渲染中增加 `ImagePart.failed` 判断 → 显示 `FailedMediaPlaceholder`
- 占位符用 CSS `aspect-ratio` + `maxWidth` 约束，不按像素渲染

---

## 十一、边缘情况处理

### 11.1 边缘场景表（17项）

| # | 场景 | 处理方式 |
|--|------|---------|
| 1 | 用户不点AI按钮直接发送 | 整段文本当1张图 |
| 2 | 编辑后格式破坏 | `_parse_image_tasks` 回退为1张图 |
| 3 | 上传多张图片 | 第1张=商品，其余=风格参考（MULTI_IMAGE_GUIDE） |
| 4 | 超过 max_images | enhance API 限制 + prompt 约束 |
| 5 | 模型内容安全拒绝 | failed ImagePart + 友好提示 |
| 6 | 普通模式要求生图 | generate_image 处理；电商需求引导切换图片模式 |
| 7 | 品类×平台矩阵无匹配 | 不注入第4层，前3层兜底 |
| 8 | enhance API 超时 | 前端30秒超时+loading恢复 |
| 9 | 连续快速点击AI按钮 | 前端防抖：点击后禁用 |
| 10 | 图片未上传完就点AI按钮 | 图生图模式下按钮置灰 |
| 11 | 编辑后又加新图片 | 提示"建议重新点击AI提示词" |
| 12 | 发送空消息 | 后端 task 为空返回错误 |
| 13 | 文本平台与参数不一致 | enhance API 从文本检测平台关键词覆盖默认值 |
| 14 | rembg 去背景失败 | 降级用原图继续 |
| 15 | GPT返回图片损坏 | Pillow.open 校验 + failed ImagePart |
| 16 | CDN上传失败 | 重试1次 + failed ImagePart |
| 17 | 品类关键词冲突 | 优先长词匹配；兜底 GPT 判断 |

### 11.2 后续迭代项（不阻塞上线）

| # | 场景 | 时机 |
|--|------|------|
| 1 | 非商品图检测 | enhance API 加 VLM 预检 |
| 2 | 低分辨率校验 | enhance API 校验≥400px |
| 3 | 历史图片查询优化 | 加索引+分页 |
| 4 | 组织级品牌模板 | org_style_templates 表 |
| 5 | CDN URL 过期 | 确认策略，加续期 |
| 6 | 多平台同时生成 | enhance API 支持 platforms[] |

---

## 十二、工具注册与系统集成

### 12.1 工具定义

```python
{
    "type": "function",
    "function": {
        "name": "image_agent",
        "description": "电商图片生成工具——生成单张商品图片。每次调用只生成1张。如需多张，拆分为多次调用。",
        "parameters": {
            "type": "object", "required": ["task"],
            "properties": {
                "task": {"type": "string", "description": "单张图片的生成描述"},
                "image_urls": {"type": "array", "items": {"type": "string"}, "description": "参考图CDN URLs"},
                "platform": {"type": "string", "enum": ["taobao","tmall","jd","pdd","douyin","xiaohongshu"]},
            },
        },
    },
}
```

### 12.2 注册配置

```python
_CORE_TOOLS:          新增 "image_agent"
_PLAN_MODE_BLOCKED:   新增 "image_agent"
_SAFETY_LEVELS:       "image_agent": SafetyLevel.CONFIRM
_CONCURRENT_SAFE:     不加入（串行）
```

### 12.3 TOOL_SYSTEM_PROMPT

```
## 图片生成（image_agent）
=== CRITICAL ===
- 图生图/文生图模式下的消息，调 image_agent 处理
- image_agent 每次只生成1张图片
- 消息中包含 image_task_meta 时，按数组遍历调用，每次传入 images[i].description 作为 task
- 不需要传 image_urls（系统自动注入）
- 每张生成后简短确认，继续下一张

## generate_image vs image_agent 分界
- 非电商画图 → generate_image
- 电商商品图 → image_agent（图片模式下强制使用）
- 普通模式下"做个主图" → 引导切换图片模式
```

---

## 十三、技术细节

### 13.1 Config

```python
# 提示词增强（enhance API）
image_enhance_model: str = "qwen3-vl-plus"             # DashScope，1元/百万tokens，创意强+能看图
image_enhance_fallback_model: str = "qwen3-vl-flash"   # 降级备选，0.15元/百万tokens
image_enhance_timeout: int = 10                         # 超时秒数

# 图片生成（ImageAgent → KIE adapter）
image_agent_kie_model: str = "gpt-image-2-text-to-image"    # 文生图默认模型
image_agent_kie_i2i_model: str = "gpt-image-2-image-to-image"  # 图生图模型
image_agent_timeout: int = 120
image_agent_max_images: int = 8
```

### 13.2 DB 迁移

```sql
-- backend/migrations/xxx_image_style_directive.sql
ALTER TABLE conversations ADD COLUMN image_style_directive text;
```

### 13.3 Adapter 改造

**OpenRouter adapter 不需要改造**（图片生成走 KIE，不走 OpenRouter）。

enhance API 使用 DashScope adapter 调用 `qwen3-vl-flash`（已有 DashScope 集成，复用 `dashscope_base_url` + API Key）。

ImageAgent 使用 KIE adapter（已有 `create_image_adapter` + `KieImageAdapter`，直接复用）。

### 13.4 新建文件

```
backend/services/agent/image/
├── __init__.py
├── image_agent.py              # 单张生成器（KIE adapter，含失败处理+retry_context）
├── image_processor.py          # rembg去背景（含失败降级）+ Pillow裁切校验
├── platform_sizes.py           # 6平台尺寸常量
├── prompts.py                  # 四层提示词（角色+12品类+6平台+8风格+矩阵）
├── prompt_builder.py           # 四层组装+品类检测+风格推荐+平台检测
└── photography_terms.py        # 摄影术语字典

backend/routes/image_routes.py  # enhance-prompt + retry 两个API
```

注意：不需要 `image_generator.py`（直接复用 `create_image_adapter` + `KieImageAdapter`）。

### 13.5 修改文件

| 文件 | 改动 |
|-----|------|
| `agent/tool_executor.py` | 注册 image_agent handler + 三重自动注入 |
| `handlers/chat_tool_mixin.py` | 注入 _current_message_images + placeholder 描述性格式 |
| `config/chat_tools.py` | 工具定义 + _CORE_TOOLS + _PLAN_MODE_BLOCKED + _SAFETY_LEVELS + TOOL_SYSTEM_PROMPT |
| `core/config.py` | 新增 image_enhance_model / image_agent_kie_model 等配置 |
| `main.py` | 注册 image_routes |
| `requirements.txt` | rembg |
| `frontend/InputArea.tsx` | SmartSubMode 新增 'image-ecom' + AI按钮+placeholder+Tab补全/标签+费用预估 |
| `frontend/ModelSelector.tsx` | 新增"电商图模式"按钮 |
| `frontend/MessageItem.tsx` | content block 渲染 failed ImagePart + 按 aspect_ratio 分组 |
| `frontend/重试逻辑` | handleRetryImage → /api/image/retry |

**不需要改的文件**（相比 v4.3 移除）：
- ~~`adapters/openrouter/chat_adapter.py`~~ — 图片生成走 KIE，不走 OpenRouter
- ~~`adapters/base.py`~~ — 不需要 ChatResponse.images 字段

### 13.6 安全配置

| 项 | 方案 | 来源 |
|---|------|------|
| 内容安全 | KIE adapter 自带过滤 | 复用 |
| 输入校验 | CDN白名单+长度上限+enum | 新增 |
| 积分扣费 | lock/confirm/refund + TTL超时退还 | 复用+增强 |
| 超时控制 | execution_budget + asyncio.wait_for | 复用 |
| 审计日志 | tool_audit.py | 复用 |
| 并发限制 | 积分锁防同一用户并发 | 复用 |

### 13.7 项目实际 gap 与改动清单

以下 7 个点是方案设计与项目实际代码的差异，实施时必须处理：

| # | Gap | 实际代码 | 需要的改动 |
|--|-----|---------|----------|
| 1 | 前端模式字段 | `smartSubMode` 类型需扩展（InputArea.tsx:83） | 新增 `'image-ecom'` 值 + ModelSelector 新增按钮 |
| 2 | sendMessage 参数 | `params: Record<string, unknown>`（messageSender.ts:29） | `image_task_meta` 通过 `params` 透传，不是独立字段 |
| 3 | CreditMixin 无 TTL | `_lock_credits(task_id, user_id, amount, reason, org_id)`（credit_mixin.py:62） | **需扩展**：增加 `ttl_seconds` 参数 + 定时清理过期锁 |
| 4 | placeholder 格式固定 | 图片固定 `📊 图表已生成...`（chat_tool_mixin.py:492） | **需改**：图片类型改为 `🖼️ {name} 已生成...` |
| 5 | ~~OpenRouter kwargs~~ | ~~已不需要~~（图片生成走 KIE，不走 OpenRouter） | **已删除**，无需改动 |
| 6 | content block 无 failed 处理 | 只判断 `part.type === 'image' && url`（MessageItem.tsx:534） | **需加**：`url === null && failed === true` 时渲染 FailedMediaPlaceholder |
| 7 | InlineChartImage 无分组网格 | 逐个 `InlineChartImage` 渲染（MessageItem.tsx:538） | **需加**：`groupImagesByRatio` 分组后传 AiImageGrid |

### 13.8 AgentResult 格式

**成功**：`AgentResult(status="success", collected_files=[{type:"image", url, width, height, alt}])`

**失败**：`AgentResult(status="error", collected_files=[{type:"image", url:null, failed:true, error, retry_context:{task, image_urls, platform, style_directive}}])`

### 13.9 平台尺寸

| 平台 | 主图 | 竖图 | 详情页宽度 |
|-----|------|------|----------|
| 淘宝/天猫 | 800×800 | 750×1000 | 750px(移动)/790px(PC) |
| 京东 | 800×800 | 750×1000 | 750px/790px |
| 拼多多 | 480×480 | — | 750px |
| 抖音 | 800×800 | 750×1000 | 750px |
| 小红书 | 800×800 | 750×1000 | 750px |

### 13.10 依赖

```
rembg>=2.0.50
Pillow>=10.0.0   # 已有
```

---

## 十四、实施计划

### Phase 1：基础设施（0.5天）
- [ ] config.py 添加 image_enhance_model / image_agent_kie_model 等配置字段
- [ ] DB 迁移：conversations 表新增 `image_style_directive` 字段
- [ ] 验证 DashScope qwen3-vl-plus 可调通（enhance API 用）
- [ ] 验证 KIE adapter 生图基线（现有功能确认可用）

### Phase 2：四层提示词系统（1.5天）
- [ ] 新建 backend/services/agent/image/ 目录
- [ ] prompts.py — 12品类+6平台(含趋势)+8风格+矩阵
- [ ] prompt_builder.py — 四层组装+品类检测+风格推荐+平台检测
- [ ] photography_terms.py + platform_sizes.py

### Phase 3：后端API + 风格持久化（1天）
- [ ] image_routes.py — enhance-prompt（含三模式风格+误判防护+费用预估+平台检测）
- [ ] image_routes.py — retry（含鉴权+积分扣费+原位替换）
- [ ] 注册路由 main.py
- [ ] 单元测试

### Phase 4：ImageAgent + 自动注入（2天）
- [ ] image_generator.py（含429限流重试）
- [ ] image_processor.py（rembg降级+Pillow校验）
- [ ] image_agent.py（单张生成+失败返回failed ImagePart+retry_context+TTL积分锁）
- [ ] tool_executor 三重注入 + ChatHandler 注入
- [ ] 工具注册（chat_tools.py + TOOL_SYSTEM_PROMPT 含分界规则）
- [ ] rembg 依赖
- [ ] 单元测试+集成测试

### Phase 5：前端交互（2天）
- [ ] AI提示词按钮（防抖+图片未上传置灰+图片变更提示）
- [ ] 模式化 placeholder
- [ ] 桌面端 Tab 补全 + 移动端标签组
- [ ] 费用预估展示
- [ ] enhance API 调用 + imageTaskMeta 透传
- [ ] **占位符尺寸适配**：从 image_task_meta 预渲染不同尺寸占位符（§10.1）
- [ ] **getPlaceholderSize** 映射函数（aspect_ratio + platform → 像素尺寸）
- [ ] content block 渲染 failed ImagePart → FailedMediaPlaceholder（含正确尺寸）
- [ ] handleRetryImage → /api/image/retry + 原位更新

### Phase 6：详情页生成（2天）
- [ ] 详情页模板系统（3-5套）
- [ ] 多区块生成+长图拼接
- [ ] GPT-5.4-Image-2 单张长图可行性验证
- [ ] 各平台尺寸输出测试

**总计约 10 天。Phase 1-5（约7.5天）上线主图生成，Phase 6 扩展详情页。**

---

## 十五、风险与降级

| 风险 | 降级方案 | 详见 |
|-----|---------|------|
| GPT-5.4-Image-2 超时 | 120s超时 + failed ImagePart + 重试 | §7.2 |
| OpenRouter 429 限流 | 指数退避重试2次 + 友好提示 | §7.3 |
| OpenRouter 宕机 | 友好错误 + 建议稍后重试 | |
| 图片质量不满意 | 用户编辑提示词重新发送 | |
| 品类检测错误 | 通用模板兜底 | §5.3 |
| 品类×平台矩阵未覆盖 | 不注入第4层，前3层有效 | §5.6 |
| 提示词格式被编辑破坏 | 回退整段当1张图 | §11.1 #2 |
| 模型内容安全拒绝 | failed ImagePart + 提示 | §9.5 |
| rembg 去背景失败 | 降级用原图继续 | §11.1 #14 |
| GPT 返回图片损坏 | Pillow 校验 + failed ImagePart | §11.1 #15 |
| CDN 上传失败 | 重试1次 + failed ImagePart | §11.1 #16 |
| 积分锁死 | lock TTL 超时自动退还 | §7.2 |
| 风格调整误判 | 肯定句式排除 + 精准关键词 | §6.3 |
| 用户不点AI按钮直接发送 | 整段当1张图处理 | §11.1 #1 |
| failedMediaType 不覆盖 chat 模式 | failed ImagePart 方案绕过 | §9.1 |
