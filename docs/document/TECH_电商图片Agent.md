# TECH_电商图片Agent 技术方案

> 版本：v2.2 | 日期：2026-04-26

## 一、需求概述

新增 **ImageAgent**（电商图片生成Agent），作为主循环的一个工具，与 ERPAgent 同级。主 Agent 负责理解用户需求、查询商品信息，然后调用 ImageAgent 执行图片生成，结果返回给主 Agent 展示给用户。

**核心场景**：
- A. 商品主图生成（白底图 / 场景图）
- B. 详情页生成（卖点图 + 排版）

**技术选型**：
- 图片生成模型：`openai/gpt-5.4-image-2`（OpenRouter）
- 背景去除：rembg（本地）
- 多尺寸裁切：Pillow
- 存储：复用现有 OSS/CDN 链路

## 二、架构设计

### 2.1 双模式调用链路

ImageAgent 支持 **plan**（方案模式）和 **execute**（执行模式）两种模式，与现有计划模式体系打通。

**强制 plan → 确认 → execute，不允许跳过。** 三层保障：

1. **工具描述约束**：tool schema description 明确写"必须先 plan，用户确认后再 execute"
2. **代码拦截**：ImageAgent execute 模式必须传 plan 参数，缺失则拒绝执行
3. **TOOL_SYSTEM_PROMPT**：全局工具指引中加 image_agent 专属规则（与 erp_agent 规则同级）

#### Plan 模式（先出方案，不生图，零消耗）

```
用户："帮我把这个商品做个淘宝主图"（附商品图+参考图）
         ↓
主 Agent（ChatHandler + ToolLoopExecutor）
  ├── 理解需求
  ├── [可选] 调 erp_agent 查商品信息
  └── 调 image_agent(mode="plan")
        ↓
ImageAgent plan 模式（调 GPT-5.4-Image-2 纯文本能力）：
  ├── 看商品图 → 识别品类（美妆）→ 加载美妆品类模板
  ├── 看参考图 → 提取风格（柔光、粉色渐变、45度角）
  ├── 看平台 → 加载淘宝规范（800×800、白底要求）
  └── 返回方案（用户可理解的描述，不暴露提示词）：
      "方案：
       ① 白底主图 — 商品居中，纯白背景，柔光箱布光，800×800
       ② 风格主图 — 参考您提供的风格，粉色渐变背景，45度角
       ③ 场景图 — 化妆台场景，自然窗光，搭配花瓣点缀
       共3张，尺寸800×800 + 750×1000"
        ↓
主 Agent 展示方案给用户
        ↓
用户确认或调整："场景换成木桌，其他OK"
```

#### Execute 模式（按确认后的方案执行生成）

```
主 Agent 调 image_agent(mode="execute", plan=确认后的方案)
        ↓
ImageAgent execute 模式（固定 Pipeline）：
  ├── Step 1: 解析方案中每张图的参数
  ├── Step 2: 为每张图构建专业提示词（5要素公式，用户不可见）
  ├── Step 3: 预处理原图（去背景等）
  ├── Step 4: 调 GPT-5.4-Image-2 生成
  ├── Step 5: 多尺寸裁切（Pillow）
  ├── Step 6: 上传 CDN
  └── 返回 AgentResult（图片URLs）
        ↓
主 Agent 展示图片给用户（复用现有 ImagePart 渲染）
```

### 2.2 与 ERPAgent 的对比

| 维度 | ERPAgent | ImageAgent |
|-----|----------|------------|
| 内部模式 | ToolLoop（LLM自主编排） | 固定 Pipeline |
| 双模式 | erp_analyze(plan) + erp_agent(execute) | image_agent(mode=plan/execute) |
| 是否需要LLM决策 | 是（路由到4个部门Agent） | plan模式需要（分析品类+风格），execute不需要 |
| 耗时 | 5-15s | plan: 3-5s / execute: 15-60s |
| 返回 | AgentResult（数据表/文本） | AgentResult（方案文本 / 图片URLs） |

### 2.3 OpenRouter 图片生成 API 协议

```python
# 请求
{
    "model": "openai/gpt-5.4-image-2",
    "messages": [
        {
            "role": "system",
            "content": "你是专业电商摄影师..."  # 三层系统提示词
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "生成一张白底电商主图..."},
                {"type": "image_url", "image_url": {"url": "https://cdn/.../商品图.jpg"}},
                {"type": "image_url", "image_url": {"url": "https://cdn/.../参考图.jpg"}}
            ]
        }
    ],
    "modalities": ["text", "image"],  # 关键：启用图片输出
    "image_config": {
        "aspect_ratio": "1:1"
    }
}

# 响应（非流式）
{
    "choices": [{
        "message": {
            "content": [
                {"type": "text", "text": "已生成白底主图..."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            ]
        }
    }],
    "usage": {"prompt_tokens": ..., "completion_tokens": ..., "cost": ...}
}
```

## 三、三层提示词架构

ImageAgent 的提示词不是"写一版就够"，而是**三层叠加、动态组装**。用户看不到提示词，只看到方案描述。

### 3.1 架构总览

```
┌─────────────────────────────────────────────┐
│ 第1层：角色 + 通用规则（固定注入 system）      │
│   专业电商摄影师身份 + 5要素公式 + 质量红线    │
├─────────────────────────────────────────────┤
│ 第2层：品类模板（按商品类型自动匹配）          │
│   服装/食品/电子/美妆/家具/珠宝/母婴 各有模板  │
├─────────────────────────────────────────────┤
│ 第3层：平台规范（按目标平台自动注入）          │
│   淘宝/京东/拼多多/抖音/小红书 各有规范        │
└─────────────────────────────────────────────┘
         ↓ 三层叠加组装
   完整的 system prompt 传给 GPT-5.4-Image-2
```

### 3.2 第1层：角色 + 通用规则（固定不变）

```python
# backend/services/agent/image/prompts.py

SYSTEM_PROMPT_BASE = """你是专业的电商产品摄影师兼美术指导。精通中英文摄影术语，
拥有丰富的商业产品摄影经验。你的任务是根据商品信息和用户需求，
生成高转化率的电商产品图片。

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
- 将风格应用到商品图上
- 商品本身的外观不可被风格改变
- 明确区分"风格参考"和"商品素材"
"""
```

### 3.3 第2层：品类模板（自动匹配）

```python
# backend/services/agent/image/prompts.py

CATEGORY_TEMPLATES = {
    "clothing": {
        "label": "服装",
        "keywords": ["衣服", "裙", "裤", "外套", "T恤", "卫衣", "衬衫", "连衣裙",
                     "西装", "羽绒服", "内衣", "袜", "帽"],
        "prompt_guide": """## 服装品类摄影指南
- 角度：正面平拍（上装）、悬挂拍（裙/大衣）、平铺拍（套装搭配）
- 光线：柔和漫射光，避免面料上的硬阴影，侧光展示面料质感
- 重点：面料纹理必须可见，展示版型和剪裁，褶皱自然不刻意
- 背景：白底（目录图）、生活场景（街拍/咖啡店）
- 道具：简约配饰点缀，不喧宾夺主
- 模特：展示上身效果时，注意垂感和贴合度""",
    },

    "food": {
        "label": "食品饮料",
        "keywords": ["食品", "零食", "茶", "咖啡", "酒", "水果", "坚果", "饮料",
                     "糕点", "蛋糕", "巧克力", "奶", "蜂蜜", "调味", "酱"],
        "prompt_guide": """## 食品品类摄影指南
- 角度：俯拍90度（拼盘/套装）、45度（成品菜/瓶装）、平视（饮品/高瓶）
- 光线：侧逆光透过柔光纱，打出食物质感和蒸汽，暖色调为主
- 重点：色泽鲜亮、质感诱人、少量分量更精致
- 背景：木桌（温馨）、大理石（高端）、亚麻布（自然）
- 道具：相关食材散落、餐具、绿植点缀
- 特殊：蒸汽/水珠增加新鲜感""",
    },

    "electronics": {
        "label": "电子产品",
        "keywords": ["手机", "电脑", "耳机", "音箱", "充电", "数据线", "键盘",
                     "鼠标", "平板", "相机", "手表", "智能", "数码"],
        "prompt_guide": """## 电子产品品类摄影指南
- 角度：三分之四视角（展示屏幕+侧面）、俯拍（耳机/配件）、正面（手机/平板）
- 光线：干净中性光，避免屏幕反光，细微边缘光勾勒轮廓
- 重点：工艺细节、接口/按键清晰、屏幕内容（如需）
- 背景：深色渐变/哑光黑（高端感）、纯白（目录图）
- 道具：极简，桌面场景可搭配咖啡/笔记本
- 特殊：金属材质注意控制反光高光""",
    },

    "cosmetics": {
        "label": "美妆护肤",
        "keywords": ["口红", "面霜", "精华", "粉底", "眼影", "腮红", "护肤",
                     "化妆", "美妆", "防晒", "面膜", "洗面奶", "香水", "卸妆"],
        "prompt_guide": """## 美妆护肤品类摄影指南
- 角度：30度微侧（展示瓶身+标签）、正面（展示包装设计）、微距（质地特写）
- 光线：双45度柔光箱，背后轮廓光增加立体感，避免瓶身过曝
- 重点：标签文字清晰可读、瓶身质感（磨砂/光面）还原准确
- 背景：柔和渐变（粉/紫/裸色系）、大理石（高端）、植物元素（天然品牌）
- 道具：花瓣、草本植物、水珠（清爽感）、丝绸（奢华感）
- 特殊：质地涂抹展示（膏体/液体的延展性）""",
    },

    "furniture": {
        "label": "家居家具",
        "keywords": ["沙发", "桌", "椅", "柜", "床", "灯", "窗帘", "地毯",
                     "书架", "置物", "花瓶", "抱枕", "挂画", "收纳"],
        "prompt_guide": """## 家居品类摄影指南
- 角度：平视（沙发/床）、俯拍（小件家居）、场景全景（展示空间感）
- 光线：模拟自然日光，大面积柔光源，晚间场景用暖色灯光
- 重点：材质纹理（木纹/布纹/金属）、尺寸比例感、使用场景
- 背景：完整房间场景，展示搭配效果
- 道具：书籍、咖啡杯、绿植、毛毯 — 增加生活气息
- 特殊：必须传达尺寸感（与已知物品对比）""",
    },

    "jewelry": {
        "label": "珠宝配饰",
        "keywords": ["项链", "戒指", "耳环", "手镯", "手链", "珠宝", "银饰",
                     "黄金", "钻石", "翡翠", "玉", "胸针", "发饰", "腰带"],
        "prompt_guide": """## 珠宝配饰品类摄影指南
- 角度：微距特写、45度（戒指）、平铺（套装）、佩戴展示（手/颈/耳）
- 光线：柔光罩全方位漫射，控制金属反光和宝石折射，避免死白高光
- 重点：工艺细节、宝石光泽和火彩、金属质感
- 背景：深色丝绒（黑/酒红）展示高级感、白底（目录图）
- 道具：极简 — 珠宝本身就是主角
- 特殊：焦点堆叠确保全程清晰，微小细节放大展示""",
    },

    "mother_baby": {
        "label": "母婴玩具",
        "keywords": ["奶瓶", "玩具", "积木", "绘本", "婴儿", "儿童", "母婴",
                     "尿不湿", "奶粉", "推车", "安全座椅", "童装"],
        "prompt_guide": """## 母婴玩具品类摄影指南
- 角度：平视（儿童视角）、45度（包装展示）、使用场景
- 光线：明亮温暖柔和光，无硬阴影（传递安全温馨感）
- 重点：色彩鲜艳准确、安全性细节、尺寸参照
- 背景：明亮纯色、儿童房/游戏室场景
- 道具：其他玩具、儿童友好的环境元素
- 特殊：体现与儿童的互动感""",
    },
}

# 默认模板（品类未识别时使用）
DEFAULT_CATEGORY_TEMPLATE = """## 通用商品摄影指南
- 角度：45度三分之四视角（展示正面+侧面）、正面平拍
- 光线：双侧45度柔光箱 + 顶部补光
- 重点：商品特征清晰可见，色彩准确
- 背景：纯白（目录图）、浅灰渐变（质感图）
- 道具：与商品使用场景相关的简约道具"""
```

### 3.4 第3层：平台规范（自动注入）

```python
# backend/services/agent/image/prompts.py

PLATFORM_PROMPTS = {
    "taobao": """## 淘宝/天猫平台规范
- 主图5张：前4张展示商品（无水印/文字/边框），第5张必须纯白底
- 风格：明亮干净，信任感强，真实展示商品
- 竖图：3:4 比例（750×1000），服饰类优先竖图
- 白底图要求：商品居中，纯白背景 #FFFFFF，柔和自然投影，商品占画面60-80%
- 文件限制：单张≤3MB""",

    "tmall": """## 天猫平台规范
- 品质感要求高于淘宝，强调品牌调性
- 主图风格统一，系列商品保持视觉一致性
- 竖图：3:4 比例（750×1000）
- 白底图要求同淘宝
- 详情页 PC 端宽度 790px""",

    "jd": """## 京东平台规范
- 首图必须纯白底，商品居中
- 品质感和专业度要求最高
- 不允许出现其他品牌商品、联系方式、外链
- 字体和图片需有版权
- 详情页强调参数和品质""",

    "pdd": """## 拼多多平台规范
- 白底图优先，商品占画面70%以上
- 风格直接清晰，强调性价比
- 无水印、无边框、无拉伸变形
- 色彩鲜明，主图要在信息流中抢眼""",

    "douyin": """## 抖音平台规范
- 强调"即时感"和"真实感"
- 缩略图需高对比度、强视觉冲击
- 风格偏年轻化，情绪化表达
- 竖图 3:4 效果更好（适配手机屏）
- 生活化场景优于纯棚拍""",

    "xiaohongshu": """## 小红书平台规范
- 强调"美感"和"生活方式"
- 风格精致，像朋友的真实分享
- 色调统一，有"种草"氛围
- 1:1 或 3:4 比例
- 场景化 > 棚拍，真实感 > 精修感""",
}
```

### 3.5 提示词构建流程

```python
# backend/services/agent/image/prompt_builder.py

class PromptBuilder:
    """三层提示词动态组装器。"""

    def build_system_prompt(
        self, category: str, platform: str,
    ) -> str:
        """组装完整的 system prompt。

        三层叠加：
        1. SYSTEM_PROMPT_BASE（角色 + 5要素 + 质量红线）
        2. CATEGORY_TEMPLATES[category]（品类模板）
        3. PLATFORM_PROMPTS[platform]（平台规范）
        """
        parts = [SYSTEM_PROMPT_BASE]

        # 第2层：品类模板
        cat_template = CATEGORY_TEMPLATES.get(category)
        if cat_template:
            parts.append(cat_template["prompt_guide"])
        else:
            parts.append(DEFAULT_CATEGORY_TEMPLATE)

        # 第3层：平台规范
        plat_prompt = PLATFORM_PROMPTS.get(platform)
        if plat_prompt:
            parts.append(plat_prompt)

        return "\n\n".join(parts)

    def detect_category(
        self, product_info: str, task: str,
    ) -> str:
        """根据商品信息和任务描述自动检测品类。

        优先用关键词匹配，匹配不到则返回 "general"。
        """
        text = f"{product_info} {task}".lower()
        for cat_key, cat_data in CATEGORY_TEMPLATES.items():
            for keyword in cat_data["keywords"]:
                if keyword in text:
                    return cat_key
        return "general"

    def build_plan_prompt(
        self,
        task: str,
        product_info: str,
        platform: str,
        image_types: list[str],
        has_reference: bool,
    ) -> str:
        """构建 plan 模式的 user prompt。

        让模型分析需求并返回方案描述（用户可读的自然语言），
        不返回生图提示词（提示词是 execute 阶段内部使用）。
        """
        parts = [
            f"## 任务\n{task}",
        ]
        if product_info:
            parts.append(f"## 商品信息\n{product_info}")
        parts.append(f"## 目标平台\n{platform}")
        parts.append(f"## 需要生成的图片类型\n{', '.join(image_types)}")
        if has_reference:
            parts.append("## 参考图\n用户已提供参考图，请分析其风格并应用到方案中。")

        parts.append("""## 输出要求
请分析以上信息，输出图片生成方案。格式要求：

对每张图给出：
- 图片类型（白底主图/场景图/卖点图/细节图 等）
- 尺寸
- 构图描述（用用户能理解的语言，如"商品居中，背景为温暖的木桌"）
- 风格描述

最后给出图片总数和预估信息。
不要输出技术性的生图提示词，用自然语言描述即可。""")

        return "\n\n".join(parts)

    def build_generation_prompt(
        self,
        image_description: str,
        product_info: str,
    ) -> str:
        """构建 execute 模式的单张图生图提示词。

        将方案中每张图的描述 + 商品信息 → 转化为 5 要素结构化提示词。
        这是内部使用的提示词，用户不可见。
        """
        return f"""请根据以下描述生成一张专业的电商产品摄影图片。

## 商品信息
{product_info}

## 图片要求
{image_description}

严格按照摄影5要素生成：主体、背景、光线、角度、风格。
确保商品准确还原，画面专业，适合电商平台使用。"""
```

### 3.6 专业摄影术语字典（提示词增强）

ImageAgent 内部使用，提升提示词专业度：

```python
# backend/services/agent/image/photography_terms.py

# 光线术语映射（中文描述 → 英文提示词关键词）
LIGHTING_TERMS = {
    "柔光": "soft diffused light, softbox lighting",
    "自然光": "natural window light, daylight",
    "逆光": "rim lighting, backlit, edge glow",
    "侧光": "side lighting, dramatic side light",
    "顶光": "overhead lighting, top-down light",
    "环形灯": "ring light, even circular lighting",
    "蝴蝶光": "butterfly lighting, paramount lighting",
    "三点布光": "three-point lighting setup",
    "暖色调": "warm golden light, warm tone",
    "冷色调": "cool neutral light, cool tone",
}

# 角度术语映射
ANGLE_TERMS = {
    "俯拍": "overhead shot, top-down view, flat lay",
    "平拍": "eye-level shot, straight-on view",
    "45度": "45-degree angle, three-quarter view",
    "仰拍": "low angle shot, hero angle",
    "微距": "macro close-up, extreme detail shot",
    "侧面": "side profile view",
}

# 构图术语映射
COMPOSITION_TERMS = {
    "居中": "centered composition, symmetrical",
    "三分法": "rule of thirds composition",
    "留白": "generous negative space, breathing room",
    "浅景深": "shallow depth of field, background bokeh",
    "对称": "symmetrical composition",
}

# 背景术语映射
BACKGROUND_TERMS = {
    "纯白": "pure white seamless background",
    "渐变": "smooth gradient background",
    "大理石": "white marble surface, marble countertop",
    "木桌": "natural wood surface, rustic wooden table",
    "丝绒": "deep velvet surface",
    "场景": "lifestyle setting, in-context scene",
    "暗调": "dark moody background, low-key",
}
```

## 四、Plan/Execute 双模式详细设计

### 4.1 Plan 模式内部流程

```
image_agent(mode="plan", task=..., image_urls=[...], product_info=...)
  ↓
1. detect_category(product_info, task)
   → 关键词匹配 → "cosmetics"
  ↓
2. build_system_prompt("cosmetics", "taobao")
   → 第1层(角色) + 第2层(美妆模板) + 第3层(淘宝规范)
  ↓
3. build_plan_prompt(task, product_info, ...)
   → 组装 user message（含商品图+参考图）
  ↓
4. 调 GPT-5.4-Image-2（纯文本模式，modalities=["text"]）
   → 模型分析图片 + 商品信息 → 输出方案
  ↓
5. 解析模型输出 → 结构化方案
  ↓
6. 返回 AgentResult(status="plan", summary=方案文本)
   → 主 Agent 展示给用户
```

### 4.2 Execute 模式内部流程（含 thinking 进度推送）

```
image_agent(mode="execute", plan=确认后的方案, image_urls=[...])
  ↓
1. 解析方案中每张图的描述
  ↓                                              用户 thinking 区域看到：
2. 对每张图（确定性 for 循环）：                  ┌─────────────────────────────┐
   a. build_generation_prompt(图片描述, 商品信息)  │ ── Image Agent ──           │
      → 内部生成 5 要素结构化提示词（用户不可见） │ → 正在生成第 1/3 张：       │
   b. 预处理（去背景，如果需要白底图）            │   白底主图 800×800...       │
   c. 调 GPT-5.4-Image-2（modalities=["image"]） │ → 第 1 张生成完成，处理中...│
      → 传入商品图 + 参考图 + 专业提示词          │ → 正在生成第 2/3 张：       │
   d. 接收 base64 图片                            │   场景图...                 │
   e. 多尺寸裁切                                  │ → 第 2 张生成完成           │
   f. 上传 CDN                                    │ → 正在生成第 3/3 张...      │
  ↓                                              │ → 完成，共 3 张图片         │
3. 返回 AgentResult(status="success",             └─────────────────────────────┘
      collected_files=[图片列表])
```

### 4.3 Thinking 进度推送（复用 ERPAgent 机制）

直接复用 ERPAgent 的 `_push_thinking()` 模式，通过 WebSocket 实时推送进度：

```python
# image_agent.py — 复用 erp_agent.py:436-453 的模式

async def _push_thinking(self, text: str) -> None:
    """实时推送进度到前端 thinking 区域。"""
    self._thinking_parts.append(f"→ {text}")
    if not self.task_id or not self.message_id:
        return
    try:
        from schemas.websocket_builders import build_thinking_chunk
        from services.websocket_manager import ws_manager
        msg = build_thinking_chunk(
            task_id=self.task_id,
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            chunk=f"\n── Image Agent ──\n→ {text}\n",
        )
        await ws_manager.send_to_task_or_user(
            self.task_id, self.user_id, msg
        )
    except Exception:
        pass
```

Pipeline 中的调用点：

```python
# image_pipeline.py

async def run(self, plan, ...) -> list[GeneratedImage]:
    results = []
    total = len(plan.items)

    await self._push_thinking(f"开始生成 {total} 张图片...")

    for i, item in enumerate(plan.items, 1):
        await self._push_thinking(f"正在生成第 {i}/{total} 张：{item.description}...")
        raw = await self._generate(item)

        await self._push_thinking(f"第 {i} 张生成完成，裁切多尺寸中...")
        processed = await self._process(raw)

        url = await self._upload(processed)
        results.append(url)

    await self._push_thinking(f"全部完成，共 {total} 张图片已上传")
    return results
```

### 4.4 TOOL_SYSTEM_PROMPT 强制规则（第3层保障）

在 `chat_tools.py` 的 `TOOL_SYSTEM_PROMPT` 中新增 image_agent 专属规则，与 erp_agent 规则同级：

```python
# chat_tools.py — TOOL_SYSTEM_PROMPT 新增段落

"""
## 图片生成（image_agent）

=== CRITICAL ===
- image_agent 强制两步走：必须先调 mode='plan' 出方案，展示给用户确认后，再调 mode='execute' 执行
- 禁止跳过 plan 直接 execute — 图片生成消耗资源，用户必须先确认方案
- plan 模式返回的方案直接展示给用户，不要修改或总结
- 用户确认后，将确认的方案原文传入 execute 的 plan 参数
- 用户要求调整方案时，重新调 mode='plan' 并将调整需求写入 task
- 如需商品信息（名称/卖点/价格），先调 erp_agent 查询，再将结果传入 product_info
- 用户上传的图片（商品原图或风格参考图）传入 image_urls
"""
```

### 4.5 工具参数设计（含 mode）

```python
{
    "type": "function",
    "function": {
        "name": "image_agent",
        "description": (
            "电商图片生成Agent——生成商品主图、详情页图片。\n"
            "能力：白底主图、场景图、详情页卖点图、多平台多尺寸适配。\n"
            "支持的平台：淘宝/天猫/京东/拼多多/抖音/小红书。\n\n"
            "## 调用流程（两步走）\n"
            "1. 先调 mode='plan' 获取生成方案，展示给用户确认\n"
            "2. 用户确认后调 mode='execute' 执行生成\n\n"
            "## 调用时机\n"
            "用户要求生成/制作/设计商品图片、主图、详情页时调用。\n"
            "如有商品原图或参考图，将 image_urls 传入。\n"
            "如需商品信息，先调 erp_agent 查询后将结果传入 product_info。"
        ),
        "parameters": {
            "type": "object",
            "required": ["task", "mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["plan", "execute"],
                    "description": (
                        "plan=分析需求输出方案（不生图，零消耗），"
                        "execute=按方案执行生成"
                    ),
                },
                "task": {
                    "type": "string",
                    "description": "图片生成任务描述（用户需求 + 风格要求）",
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "用户上传的图片CDN URLs（商品原图和/或风格参考图）",
                },
                "product_info": {
                    "type": "string",
                    "description": "商品信息（名称、卖点、价格等，从ERP查询获得）",
                },
                "platform": {
                    "type": "string",
                    "enum": ["taobao", "tmall", "jd", "pdd", "douyin", "xiaohongshu"],
                    "description": "目标电商平台，默认 taobao",
                },
                "image_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["main", "detail", "white_bg", "scene"],
                    },
                    "description": "要生成的图片类型",
                },
                "plan": {
                    "type": "string",
                    "description": "execute模式必传：用户确认后的方案（从plan模式的返回结果中获取）",
                },
            },
        },
    },
}
```

## 五、平台尺寸规范

### 5.1 主图尺寸

| 平台 | 标准尺寸 | 竖图尺寸 | 白底要求 |
|-----|---------|---------|---------|
| 淘宝/天猫 | 800×800 | 750×1000 | 第5张必须白底 |
| 京东 | 800×800 | 750×1000 | 首图必须纯白底 |
| 拼多多 | 480×480 | — | 必须白底 |
| 抖音 | 800×800 | 750×1000 | 部分类目要求 |

### 5.2 详情页尺寸

| 平台 | 移动端宽度 | PC端宽度 | 单图最大高度 |
|-----|-----------|---------|------------|
| 淘宝 | 750px | 750px | 1200px |
| 天猫 | 750px | 790px | 1300px |
| 京东 | 750px | 790px | — |
| 拼多多 | 750px | — | 1500px |
| 抖音 | 750px | — | — |

### 5.3 尺寸常量定义

```python
# backend/services/agent/image/platform_sizes.py

PLATFORM_SIZES = {
    "taobao": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": 1200,
    },
    "tmall": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": 1300,
    },
    "jd": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
    "pdd": {
        "main": [(480, 480)],
        "detail_width": 750,
        "detail_max_height": 1500,
    },
    "douyin": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
    "xiaohongshu": {
        "main": [(800, 800), (750, 1000)],
        "detail_width": 750,
        "detail_max_height": None,
    },
}
```

## 六、文件清单与函数路径

### 6.1 新建文件

```
backend/services/agent/image/              # ImageAgent 子目录
├── __init__.py
├── image_agent.py                         # ImageAgent 主类（plan/execute双模式）
├── image_pipeline.py                      # Pipeline 步骤编排（execute模式）
├── image_generator.py                     # OpenRouter 图片生成封装
├── image_processor.py                     # 背景去除 + 裁切 + 拼接
├── platform_sizes.py                      # 平台尺寸常量
├── prompts.py                             # 三层提示词（角色+品类+平台）
├── prompt_builder.py                      # 提示词动态组装器
└── photography_terms.py                   # 专业摄影术语字典
```

### 6.2 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `backend/services/adapters/openrouter/chat_adapter.py` | `chat_sync()` 支持 `modalities` + `image_config`，响应解析 base64 图片 |
| `backend/services/adapters/base.py` | ChatResponse 添加 `images` 字段 |
| `backend/services/agent/tool_executor.py` | 注册 `image_agent` handler |
| `backend/config/chat_tools.py` | 添加 `image_agent` 工具定义 + `_CORE_TOOLS` + `_PLAN_MODE_BLOCKED` + `_SAFETY_LEVELS` |
| `backend/core/config.py` | 添加 `image_agent_model` / `image_agent_timeout` 配置字段 |
| `backend/requirements.txt` | 添加 `rembg` 依赖 |

### 6.3 核心类设计

#### ImageAgent（主类 — plan/execute 双模式）

```python
# backend/services/agent/image/image_agent.py

class ImageAgent:
    """电商图片生成 Agent — plan/execute 双模式。"""

    def __init__(
        self, db, user_id: str, conversation_id: str,
        org_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        self.message_id = message_id
        self._prompt_builder = PromptBuilder()

    async def execute(self, task: str, mode: str = "plan", **kwargs) -> AgentResult:
        """执行图片生成任务。

        Args:
            task: 用户任务描述
            mode: "plan"（出方案）| "execute"（执行生成）
            kwargs:
                image_urls: list[str]    — 用户上传的图片CDN URLs
                product_info: str        — ERP商品信息
                platform: str            — 目标平台
                image_types: list[str]   — 生成类型
                plan: str                — execute模式：确认后的方案

        Returns:
            AgentResult
        """
        if mode == "plan":
            return await self._plan(task, **kwargs)
        elif mode == "execute":
            return await self._execute(task, **kwargs)
        else:
            return AgentResult(status="error", summary=f"未知模式: {mode}")

    async def _plan(self, task: str, **kwargs) -> AgentResult:
        """方案模式：分析需求，输出方案（不生图）。"""
        product_info = kwargs.get("product_info", "")
        image_urls = kwargs.get("image_urls", [])
        platform = kwargs.get("platform", "taobao")
        image_types = kwargs.get("image_types", ["main"])

        # 1. 自动检测品类
        category = self._prompt_builder.detect_category(product_info, task)

        # 2. 组装三层系统提示词
        system_prompt = self._prompt_builder.build_system_prompt(category, platform)

        # 3. 构建 plan user prompt
        user_prompt = self._prompt_builder.build_plan_prompt(
            task, product_info, platform, image_types,
            has_reference=len(image_urls) > 0,
        )

        # 4. 调 GPT-5.4-Image-2（纯文本模式，只分析不生图）
        generator = ImageGenerator()
        plan_text = await generator.analyze(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_urls=image_urls,
        )

        # 5. 返回方案
        return AgentResult(
            status="plan",
            summary=plan_text,
            source="image_agent",
            metadata={"mode": "plan", "category": category, "platform": platform},
        )

    async def _execute(self, task: str, **kwargs) -> AgentResult:
        """执行模式：按方案生成图片。"""
        plan = kwargs.get("plan", "")
        if not plan:
            return AgentResult(status="error", summary="缺少确认后的方案", source="image_agent")

        pipeline = ImagePipeline(
            user_id=self.user_id,
            org_id=self.org_id,
            prompt_builder=self._prompt_builder,
        )
        images = await pipeline.run(
            plan=plan,
            product_info=kwargs.get("product_info", ""),
            image_urls=kwargs.get("image_urls", []),
            platform=kwargs.get("platform", "taobao"),
        )
        return self._build_result(images)
```

## 七、Adapter 改造

### 7.1 OpenRouterChatAdapter.chat_sync 扩展

```python
# 在 chat_sync 方法中支持 modalities 和 image_config

async def chat_sync(
    self,
    messages: List[Dict[str, Any]],
    reasoning_effort: Optional[str] = None,
    thinking_mode: Optional[str] = None,
    **kwargs,
) -> ChatResponse:
    request_body: Dict[str, Any] = {
        "model": self._model_id,
        "messages": messages,
        "stream": False,
    }

    # --- 新增：图片生成支持 ---
    modalities = kwargs.get("modalities")
    if modalities:
        request_body["modalities"] = modalities

    image_config = kwargs.get("image_config")
    if image_config:
        request_body["image_config"] = image_config
    # --- 新增结束 ---

    # ... 发送请求不变 ...

    # 响应解析扩展：支持多模态内容
    content = ""
    images = []
    finish_reason = None
    if choices:
        msg = choices[0].get("message", {})
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            # 多模态响应：[{type: "text", ...}, {type: "image_url", ...}]
            text_parts = []
            for part in raw_content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    images.append(part["image_url"]["url"])
            content = "\n".join(text_parts)
        else:
            content = raw_content
        finish_reason = choices[0].get("finish_reason")

    resp = ChatResponse(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )
    resp.images = images  # 扩展字段
    return resp
```

### 7.2 ChatResponse 扩展

```python
# backend/services/adapters/base.py

@dataclass
class ChatResponse:
    content: str
    finish_reason: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    images: list[str] = field(default_factory=list)  # 新增：base64图片URLs
```

## 八、工具注册

### 8.1 _CORE_TOOLS 注册

```python
_CORE_TOOLS: Set[str] = {
    "erp_agent",
    "erp_analyze",
    "image_agent",           # 新增
    "search_knowledge",
    "web_search",
    "social_crawler",
    "generate_image",
    "generate_video",
    "code_execute",
    # ... file tools, ask_user 等
}
```

### 8.2 _PLAN_MODE_BLOCKED

```python
_PLAN_MODE_BLOCKED: Set[str] = {
    "erp_agent",
    "image_agent",           # 新增
    "generate_image",
    "generate_video",
    "social_crawler",
}
```

### 8.3 SafetyLevel

```python
_SAFETY_LEVELS: Dict[str, SafetyLevel] = {
    "image_agent": SafetyLevel.CONFIRM,   # 新增
    "generate_image": SafetyLevel.CONFIRM,
    "generate_video": SafetyLevel.CONFIRM,
    # ...
}
```

### 8.4 tool_executor.py handler

```python
# __init__ 中添加
self._handlers["image_agent"] = self._image_agent

# handler 方法
async def _image_agent(self, args: Dict[str, Any]) -> "AgentResult":
    from services.agent.image.image_agent import ImageAgent

    agent = ImageAgent(
        db=self.db,
        user_id=self.user_id,
        conversation_id=self.conversation_id,
        org_id=self.org_id,
        task_id=getattr(self, "_task_id", None),
        message_id=getattr(self, "_message_id", None),
    )
    return await agent.execute(
        task=args.get("task", ""),
        mode=args.get("mode", "plan"),
        image_urls=args.get("image_urls", []),
        product_info=args.get("product_info", ""),
        platform=args.get("platform", "taobao"),
        image_types=args.get("image_types", ["main"]),
        plan=args.get("plan", ""),
    )
```

## 九、Config 配置

```python
# backend/core/config.py 新增

image_agent_model: str = "openai/gpt-5.4-image-2"
image_agent_timeout: int = 120   # 图片生成超时（秒）
image_agent_max_images: int = 8  # 单次最大生成张数
```

## 十、AgentResult 返回格式

### Plan 模式返回

```python
AgentResult(
    status="plan",
    summary="## 图片生成方案\n\n① 白底主图 — 商品居中...\n② 场景图 — ...",
    source="image_agent",
    metadata={"mode": "plan", "category": "cosmetics", "platform": "taobao"},
)
```

### Execute 模式返回

```python
AgentResult(
    status="success",
    summary="已生成淘宝主图 3 张（800×800 白底图 + 750×1000 竖图 + 场景图）",
    source="image_agent",
    format=OutputFormat.TEXT,
    collected_files=[
        {
            "type": "image",
            "url": "https://cdn.everydayai.com.cn/org/.../main_800x800.png",
            "width": 800,
            "height": 800,
            "alt": "商品主图 800×800 白底",
        },
        # ...
    ],
    metadata={
        "mode": "execute",
        "platform": "taobao",
        "model": "openai/gpt-5.4-image-2",
        "generation_time_s": 23.5,
    },
)
```

前端通过 `collected_files` 中的 ImagePart 自动渲染为网格 + 预览 + 下载，**零前端改动**。

## 十一、实施计划

### Phase 1：基础设施（1天）
- [ ] `backend/core/config.py` 添加 image_agent 配置字段
- [ ] `backend/services/adapters/openrouter/chat_adapter.py` 扩展 `modalities` + `image_config`
- [ ] `backend/services/adapters/base.py` ChatResponse 添加 `images` 字段
- [ ] 单元测试：验证 OpenRouter 图片生成 API 调通

### Phase 2：三层提示词系统（1天）
- [ ] 新建 `backend/services/agent/image/` 目录
- [ ] 实现 `prompts.py` — 角色基础 + 7品类模板 + 6平台规范
- [ ] 实现 `photography_terms.py` — 摄影术语字典
- [ ] 实现 `prompt_builder.py` — 三层组装 + 品类检测
- [ ] 实现 `platform_sizes.py` — 平台尺寸常量

### Phase 3：ImageAgent 核心（2天）
- [ ] 实现 `image_generator.py` — OpenRouter 图片生成封装
- [ ] 实现 `image_processor.py` — 去背景 + 裁切
- [ ] 实现 `image_pipeline.py` — Pipeline 编排
- [ ] 实现 `image_agent.py` — plan/execute 双模式主类
- [ ] 单元测试

### Phase 4：工具注册与集成（1天）
- [ ] `backend/config/chat_tools.py` 添加工具定义 + 注册
- [ ] `backend/services/agent/tool_executor.py` 注册 handler
- [ ] `backend/requirements.txt` 添加 rembg
- [ ] 集成测试 + 端到端测试

### Phase 5：详情页生成（2天）
- [ ] 详情页模板系统（3-5套固定模板）
- [ ] 多区块图片生成（主图 + 卖点图 + 细节图）
- [ ] 长图拼接（Pillow 纵向拼接）
- [ ] 测试各平台尺寸输出

**总计约 7 天**。Phase 1-4 完成即可上线主图生成，Phase 5 扩展详情页。

## 十二、依赖

```
rembg>=2.0.50        # 背景去除
Pillow>=10.0.0       # 图片处理（项目已有）
```

## 十三、安全与内部架构

### 13.1 安全配置总览

| 安全项 | 方案 | 复用/新建 |
|-------|------|----------|
| **内容安全（NSFW）** | 依赖 GPT-5.4-Image-2 自带内容安全过滤（OpenAI policy） | 复用模型能力 |
| **输入校验** | CDN URL 白名单 + 文本长度上限 + enum 校验 | 新增 |
| **积分扣费** | lock→execute→confirm/refund 原子模式 | 复用 `CreditMixin` |
| **超时控制** | `execution_budget.py` 主Agent fork budget | 复用 |
| **审计日志** | `tool_audit.py` 工具级审计 | 复用（自动覆盖） |
| **并发限制** | 积分锁防止同一用户同时多个生图任务 | 复用 `credit_lock` |
| **结果截断** | `tool_result_envelope.py` 输出信号层 | 复用 |
| **请求缓存** | 不适用（图片生成每次结果不同） | 不使用 |

### 13.2 输入校验

```python
# image_agent.py — execute() 入口处校验

def _validate_input(self, task: str, mode: str, **kwargs) -> str | None:
    """输入校验，返回错误信息或 None（通过）。"""

    # 1. mode 校验
    if mode not in ("plan", "execute"):
        return f"未知模式: {mode}"

    # 2. execute 必须有 plan
    if mode == "execute" and not kwargs.get("plan"):
        return "execute 模式必须传入确认后的方案（plan 参数）"

    # 3. task 长度限制（防 prompt injection）
    if len(task) > 2000:
        return "任务描述过长，请精简到 2000 字以内"

    # 4. image_urls 校验（CDN 白名单，防 SSRF）
    allowed_domains = {"cdn.everydayai.com.cn", "img.everydayai.com.cn"}
    for url in kwargs.get("image_urls", []):
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host not in allowed_domains:
            return f"不支持的图片来源: {host}，请使用平台上传的图片"

    # 5. 图片数量限制
    image_types = kwargs.get("image_types", ["main"])
    if len(image_types) > self._max_images:
        return f"单次最多生成 {self._max_images} 张图片"

    return None  # 通过
```

### 13.3 积分扣费流程

照搬 `MediaToolMixin._generate_image` 的 lock/confirm/refund 模式：

```python
# image_pipeline.py — execute 阶段

async def run(self, plan, ...) -> list[GeneratedImage]:
    total = len(plan.items)

    # 1. 预估并锁定积分（按总张数计算）
    credits_needed = self._estimate_credits(total)
    tx_id = self._lock_credits(
        task_id=f"img_agent_{uuid4().hex[:12]}",
        user_id=self.user_id,
        amount=credits_needed,
        reason=f"ImageAgent: {total}张图片",
        org_id=self.org_id,
    )

    try:
        results = []
        for i, item in enumerate(plan.items, 1):
            await self._push_thinking(f"正在生成第 {i}/{total} 张...")
            raw = await self._generate(item)
            processed = await self._process(raw)
            url = await self._upload(processed)
            results.append(url)

        # 2. 全部成功 → 确认扣费
        self._confirm_deduct(tx_id)
        return results

    except Exception as e:
        # 3. 任何失败 → 退还积分
        self._refund_credits(tx_id)
        raise
```

### 13.4 并发控制

**不需要额外开发**。现有机制已覆盖：

```
用户A 浏览器窗口1：调 image_agent → lock_credits(100积分) → 执行中...
用户A 浏览器窗口2：调 image_agent → lock_credits(100积分) → 余额不足 → 拒绝
```

积分锁（`credit_lock`）天然防并发 — 第一个任务锁住积分后，第二个任务因余额不足被拒绝。无需额外的分布式锁或信号量。

### 13.5 超时控制

复用 `execution_budget.py`，主Agent fork budget 给 ImageAgent：

```python
# tool_executor.py — _image_agent handler

async def _image_agent(self, args):
    agent = ImageAgent(...)
    # 注入 budget（如果主 Agent 有）
    if hasattr(self, "_budget") and self._budget:
        agent._budget = self._budget
    return await agent.execute(...)
```

ImageAgent 内部用 `asyncio.wait_for` 包裹整个 pipeline：

```python
# image_agent.py

async def _execute(self, task, **kwargs):
    timeout = min(
        self._budget.remaining if self._budget else 120,
        get_settings().image_agent_timeout,
    )
    try:
        return await asyncio.wait_for(self._run_pipeline(...), timeout=timeout)
    except asyncio.TimeoutError:
        return AgentResult(status="timeout", summary=f"图片生成超时（{timeout:.0f}秒）")
```

### 13.6 复用矩阵总览

```
已有安全层（6 Phase 全部完成）     ImageAgent 复用方式
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
tool_result_envelope.py            AgentResult 自动走信号层 ✅
context_compressor.py              主Agent循环内自动生效 ✅
execution_budget.py                主Agent fork budget 传入 ✅
tool_result_cache.py               图片生成不缓存 ⏭️ 跳过
erp_tool_executor.py (幂等)        积分锁已覆盖 ✅
tool_audit.py                      工具级审计自动覆盖 ✅
```

## 十四、风险与降级

| 风险 | 降级方案 |
|-----|---------|
| GPT-5.4-Image-2 超时 | 120s超时 + 返回错误提示，用户可重试 |
| 图片质量不满意 | 返回结果后用户可要求重新生成（主Agent循环内） |
| 品类检测错误 | 用通用模板兜底，不影响功能 |
| 提示词效果不理想 | 持续迭代品类模板，积累最佳实践 |
| rembg 去背景效果差 | 后续可替换为 BRIA-RMBG-2.0 或 API 方案 |
| OpenRouter 不稳定 | 后续可加备选 API（通义万相等） |
