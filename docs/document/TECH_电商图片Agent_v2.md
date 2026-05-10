# 电商图 AI 生成方案 v2

> 版本：v2.2 | 日期：2026-05-10
> 状态：方案设计阶段（基于 gpt-image-2 官方文档 + Coze 工作流 + 行业实测数据调研）

## 一、问题定义

### 现状问题

当前电商图模式（v1）的核心链路是：
```
用户输入描述 → 千问写"拍摄指令"（四层模板叠加） → gpt-image-2 按指令生成图
```

存在三个根本问题：

1. **产品理解缺失**：千问收到的是 12 个品类模板 + 8 个风格预设的组合，不是真正理解产品。输出的提示词像"填空题"——换任何产品结构几乎一样。

2. **图片类型错误**：一直在做"产品摄影"（棚拍、白底、光线参数），但电商平台实际需要的是**营销设计图**（产品+卖点文字+促销信息+排版布局）。

3. **提示词与模型不匹配**：给 gpt-image-2 写的中文结构化摄影指令（"双侧45度柔光箱""色温5200K偏暖"），模型不理解这种格式。gpt-image-2 理解的是自然语言描述（"soft studio lighting from upper left, warm golden tones"）。

### 目标

用户上传产品参考图 + 填写产品信息 → 千问 VL 看图理解产品并策划方案 → **一步到位**输出 gpt-image-2 可直接执行的 prompt → 逐张生成一整套平台适配的电商营销主图（+ 可选详情页）。

### 核心原则

1. **一步到位**：千问 VL 的输出就是 gpt-image-2 的执行指令，没有中间翻译层
2. **约束结构，开放内容**：输出格式固定（JSON），内容由千问根据产品自主判断
3. **对齐模型语言**：prompt 遵循 gpt-image-2 官方最佳实践，不用摄影参数

---

## 二、核心流程

### 2.1 产品信息定义

**核心原则**：产品信息的每个字段都必须**直接影响生图结果**。不收集"为了完整而完整"的信息。

每个字段的定位：

| 字段 | 类型 | 它决定了什么 | 不填会怎样 |
|------|------|-------------|-----------|
| **产品图** | 必填 | gpt-image-2 的 image-to-image 参考源。决定产品长什么样、什么颜色、什么材质。没有图就没有"图生图" | 无法生成 |
| **风格参考图** | 选填 | 用户上传喜欢的竞品主图/设计风格图。千问分析其风格元素（色调、排版、氛围），gpt-image-2 参照该风格生成。相当于告诉 AI"我想要这种感觉" | 千问根据平台规则自主判断风格 |
| **产品名称** | 必填 | 主图标题文案的核心词。如钩子图标题「56色拼豆收纳盒」直接来自这个字段 | 千问只能写"收纳盒"这种泛化词，失去辨识度，文案没有销售力 |
| **目标平台** | 必填 | 决定整套图的张数、风格方向、排版密度、尺寸。淘宝要高饱和直给，小红书要种草氛围——完全不同的策略 | 默认淘宝，但可能和用户实际需求不匹配 |
| **核心卖点** | 选填 | 决定每张图的文案内容和营销角度。"4层大容量 装下200+瓶"会变成卖点图的标题 | 千问根据图片推断（可能不精准，如把"多色"当卖点而用户想强调"大容量"） |
| **价格/促销** | 选填 | 决定是否生成**促销图**（带价格/满减/赠品信息的那种图）。"¥39.9 限时特惠"会出现在促销图的价格区域 | 不生成促销图，但**不影响其他营销图**——钩子图、卖点图、场景图都正常生成，只是没有价格相关的那一张 |
| **目标用户** | 选填 | 决定场景图的场景选择和整体调性。"宝妈"→温馨家庭场景；"设计师"→工作室场景 | 千问根据品类推断（通常够用） |
| **补充说明** | 选填 | 兜底的自由文本。用户觉得重要但上面没覆盖的信息，如"我们的Logo是蓝色的""包装盒是牛皮纸的" | 无影响 |

**不收集的信息**（看起来有用但不影响生图）：
- 品牌故事/公司介绍 → 不影响主图视觉
- SKU 规格参数（长宽高重量）→ 主图不展示数字参数（那是详情页 D7 的事，v2 不做）
- 库存/发货地/售后政策 → 不影响图片内容
- 竞品链接 → 有价值但增加复杂度，v3 再考虑

### 2.2 输入表单设计

```
┌─────────────────────────────────────────────────────────────┐
│                     产品信息                                  │
│                                                             │
│  产品图 *（你的产品长什么样）                                  │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────────┐                 │
│  │  📷  │ │  📷  │ │  📷  │ │  + 添加图片 │                 │
│  │ 正面  │ │ 侧面  │ │ 细节  │ │  (最多5张)  │                 │
│  └──────┘ └──────┘ └──────┘ └────────────┘                 │
│  ℹ️ 手机随拍即可，多角度效果更好                               │
│                                                             │
│  风格参考图（可选，你想要什么风格）                             │
│  ┌──────┐ ┌──────┐ ┌────────────┐                           │
│  │  🎨  │ │  🎨  │ │  + 添加参考 │                           │
│  │ 参考1 │ │ 参考2 │ │  (最多3张)  │                           │
│  └──────┘ └──────┘ └────────────┘                           │
│  ℹ️ 上传你喜欢的竞品主图或设计风格图，AI会参照这个风格          │
│                                                             │
│  产品名称 *     [56色拼豆收纳盒________________]              │
│                 ℹ️ 会出现在主图标题中                          │
│                                                             │
│  目标平台 *     [▾ 淘宝/天猫 ]                               │
│                                                             │
│  ── 填写更多信息（效果更好）──────────────── ▾ 展开 ──        │
│                                                             │
│  核心卖点       [4层大容量，装下200+瓶________]               │
│                 ℹ️ 你最想让买家知道的1-3个优势                 │
│                                                             │
│  价格/促销      [¥39.9 限时特惠 前100名送色卡__]              │
│                 ℹ️ 填了会生成促销图，不填则跳过                │
│                                                             │
│  目标用户       [手工DIY爱好者、宝妈___________]              │
│                 ℹ️ 影响场景图的场景选择                        │
│                                                             │
│  补充说明       [____________________________]               │
│                 ℹ️ 其他你觉得重要的信息                        │
│                                                             │
│  ── 生成设置 ──                                              │
│  生成范围：[✓] 主图（5张）    [✓] 详情页（6张）               │
│  图片尺寸：● 800×800  ○ 1024×1024  ○ 竖版1024×1536  ○ 自定义 │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│              [生成方案]  预估积分：40                          │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 信息传递给千问的方式

前端表单 → 后端组装为结构化 user message → 注入千问：

```python
def build_user_message(
    product_name: str,                    # 必填
    platform: str,                        # 必填
    product_image_count: int = 1,         # 产品图数量
    style_ref_count: int = 0,             # 风格参考图数量
    selling_points: str = "",             # 选填
    price_info: str = "",                 # 选填
    target_user: str = "",                # 选填
    extra_notes: str = "",                # 选填
    image_size: str = "800×800",          # 用户选择的尺寸
    generate_detail: bool = True,         # 是否生成详情页
) -> str:
    """将表单字段组装为千问的 user message。"""
    parts = [f"## 产品信息\n- 产品名称：{product_name}"]

    if selling_points:
        parts.append(f"- 核心卖点：{selling_points}")
    if price_info:
        parts.append(f"- 价格/促销：{price_info}")
    if target_user:
        parts.append(f"- 目标用户：{target_user}")
    if extra_notes:
        parts.append(f"- 补充说明：{extra_notes}")

    # 图片角色标注
    parts.append(f"\n## 上传的图片")
    parts.append(f"- 产品图：{product_image_count}张（用于识别产品外观，生成时保留产品）")
    if style_ref_count > 0:
        parts.append(f"- 风格参考图：{style_ref_count}张（用户希望参照这些图的风格/色调/排版/氛围来设计）")
        parts.append("- ⚠️ 注意区分：产品图 = 产品长什么样；风格参考图 = 用户想要什么风格。分析风格参考图的色调、排版、氛围、设计手法，应用到你的方案中")

    parts.append(f"\n## 生成要求")
    parts.append(f"- 图片尺寸：{image_size}")

    scope_parts = ["主图"]
    if generate_detail:
        scope_parts.append("详情页（D1首屏+D2-D3卖点+D4实拍+D5特写+D6场景）")
    parts.append(f"- 生成范围：{'，'.join(scope_parts)}")

    if not price_info:
        parts.append("- 注意：用户未提供价格信息，不要生成促销图（带价格/满减的图），用卖点图或场景图替补")

    return "\n".join(parts)
```

**示例输出**（有风格参考图时，发给千问的 user message）：
```
## 产品信息
- 产品名称：56色拼豆收纳盒
- 核心卖点：4层大容量，装下200+瓶，透明可视快速找色
- 价格/促销：¥39.9 限时特惠 前100名送色卡
- 目标用户：手工DIY爱好者、宝妈

## 上传的图片
- 产品图：3张（用于识别产品外观，生成时保留产品）
- 风格参考图：2张（用户希望参照这些图的风格/色调/排版/氛围来设计）
- ⚠️ 注意区分：产品图 = 产品长什么样；风格参考图 = 用户想要什么风格。分析风格参考图的色调、排版、氛围、设计手法，应用到你的方案中

## 生成要求
- 图片尺寸：800×800
- 生成范围：主图，详情页（D1首屏+D2-D3卖点+D4实拍+D5特写+D6场景）
```
                         ↓
┌─────────────────────────────────────────────────────────────┐
│          千问 VL（一步到位：理解 + 策划 + 输出 prompt）        │
│                                                             │
│  System Prompt 三层注入：                                    │
│  ┌────────────────────────────────────────────┐             │
│  │ ① 角色层：电商营销策划 + gpt-image-2 执行者  │             │
│  │ ② 知识层：平台规则（配置文件）+ 品类启发     │             │
│  │ ③ 输出层：严格 JSON 格式约束                │             │
│  └────────────────────────────────────────────┘             │
│                                                             │
│  千问看图理解产品 → 策划营销方案 → 直接输出 JSON               │
│  每张图的 prompt 已经是 gpt-image-2 可执行格式                │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                  前端方案卡片展示                              │
│                                                             │
│  展示每张图的角色、目的、文案                                  │
│  用户可编辑文案 → 实时更新 JSON 中对应的 prompt                │
│  确认后发送生成                                               │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│             逐张调用 gpt-image-2 image-to-image               │
│                                                             │
│  每张图 = 参考图 + prompt + quality="high" + 对应尺寸          │
│  通过 WS image_partial_update 逐张返回前端                    │
└─────────────────────────────────────────────────────────────┘
```

**与 v2.1 的关键区别**：删除了"阶段2：提示词翻译（规则引擎）"。千问直接输出 gpt-image-2 的 prompt，不需要 `prompt_translator.py`。

---

## 三、gpt-image-2 能力边界（调研结论）

> 来源：[OpenAI 官方 Prompting Guide](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)、[fal.ai 实测指南](https://fal.ai/learn/tools/prompting-gpt-image-2)、[中文渲染深度分析](https://help.apiyi.com/en/why-gpt-image-2-more-popular-than-1-5-chinese-character-rendering-en.html)

### 3.1 prompt 最佳实践（官方）

**推荐结构**：`Scene → Subject → Important details → Use case → Constraints`

**官方铁律**：
1. image-to-image 模式由 **API 端点**决定（`images.edit`），不是 prompt 中的某句话触发
2. 编辑类 prompt 必须用 **"Preserve X, change only Y"** 结构——明确说保留什么、改什么
3. 多参考图必须用 **"Image 1: ... Image 2: ..."** 索引标注角色，模型不会自动推断
4. 前 50 个词权重最高——把保留指令、风格、主体放前面
5. 具体视觉描述 > 抽象概念（❌"高端大气" ✅"dark gradient background, soft rim lighting"）
6. 引用文字用引号包裹 + 指定字体/颜色/位置 + "verbatim — no extra characters"
7. 约束放最后："no watermark, no text, no extra elements"
8. 加 "photorealistic" 可强触发照片级质感
9. 摄影参数（F2.8、5200K）会被松散解释——用自然描述代替（如 "soft depth-of-field blur"）
10. quality="high" 用于文字渲染场景——提升小字、密集排版、多字体的清晰度

**反模式（避免）**：
- 模糊赞美词："stunning, incredible, epic, masterpiece"
- 无视觉目标的风格标签："minimalist brutalist editorial"
- 情绪语言淹没实际 brief
- 摄影参数："双侧45度柔光箱""色温5200K"
- 一次性巨型 prompt——应该迭代微调

### 3.2 中文文字渲染能力（实测数据）

gpt-image-2 对比 1.5 有代际飞跃：

| 场景 | gpt-image-1.5 | gpt-image-2 |
|------|---------------|-------------|
| 常用汉字（3500-6000字） | ~80% | **~99%** |
| 4-12字标题（"一盒搞定"） | ~70-80% | **~99%** |
| 8-15字长标题 | 不可靠 | **~95%** |
| 复杂笔画字（15画+） | 不可辨认 | **90-95%** |
| 中英混排 | 间距失调 | **自然准确** |
| 特殊符号（¥、°C、★） | 不可靠 | **准确可靠** |
| <5pt 小字 | 废的 | **仍然废的** |
| 可直接发布比例 | ~30% | **~85%** |

**结论**：电商主图标题（通常 4-12 字）在最稳定区间，中文渲染**可用**。

**中文渲染最佳实践**：
1. 用 `「」` 或引号包裹：`Title: 「春节大促」` 而不是 `标题是春节大促`
2. 指定字体风格：`bold sans-serif Chinese font` 或 `思源黑体 Heavy`
3. 加质量锚定词：`high-fidelity Chinese typography, crisp text rendering`
4. quality 设为 `"high"`——文字密集时必须用 high
5. 保持简短：标题 ≤12 字，副标题 ≤15 字，避免长段落

### 3.3 image-to-image 能力

- 支持最多 **10 张参考图**（KIE 适配层限制为 16 张）
- 默认高保真度（gpt-image-2 无 input_fidelity 参数，永远是高保真）
- 保留指令有效："Preserve the product appearance, change only the background"
- 支持 mask 精确控制编辑区域（可选）
- 产品保留度：规则外形（盒子/瓶子）高、柔性产品（服装/食品）低

### 3.4 分辨率与技术约束

| 参数 | 约束 |
|------|------|
| 最大单边 | 3840px |
| 最小像素 | 655,360（约 810×810） |
| 最大像素 | 8,294,400（约 2880×2880） |
| 边长 | 必须是 16 的倍数 |
| 宽高比 | ≤ 3:1 |
| 推荐可靠上限 | 2560×1440（2K） |
| quality 参数 | low / medium / high |
| n 参数 | 1-10（同 prompt 多变体，不是不同 prompt 批量） |

**电商尺寸选项**（用户可选，默认 800×800）：

| 选项 | 实际生成尺寸 | 适用场景 |
|------|-------------|---------|
| 800×800（默认） | 800×800（1:1） | 淘宝/京东/拼多多主图标准尺寸 |
| 1024×1024 | 1024×1024（1:1） | 高清主图，需要更多细节 |
| 1024×1536 | 1024×1536（3:4） | 抖音/小红书竖图、详情页竖版 |
| 自定义 | 用户输入宽×高 | 特殊需求（边长须为16的倍数，≤3840px） |

- 默认值：主图 800×800，详情页 1024×1536
- 用户选了尺寸后，该尺寸同时应用于主图和详情页（详情页默认竖版）
- quality：`"high"`（文字渲染需要）

### 3.5 批量生成策略

**调研结论**：每张图独立 prompt + 独立调用，不是一张大图裁剪。

原因：
- gpt-image-2 不支持生成超大图分区——分辨率上限 3840px 单边
- API 的 `n` 参数是同 prompt 多变体（A/B 测试用），主图5张的 prompt 各不相同
- Coze 电商工作流也是循环节点逐张生成
- 风格一致性靠 **prompt 中的视觉锚点描述**保证，不靠单图裁剪

---

## 四、System Prompt 三层设计

### 4.1 设计理念

**"约束结构，开放内容"**——

- **结构是固定的**：输出格式（JSON）、每张图必须有的字段、prompt 的组织方式
- **内容是开放的**：产品是什么、卖点是什么、背景用什么——千问根据看到的图+文案自主判断
- **知识是注入的**：平台规则、gpt-image-2 的能力边界——作为参考知识给千问

不需要 12 个品类模板。千问 VL 看到产品图后，天然就知道品类、材质、用法、用户群体。v1 的品类模板是**穷举**逻辑（列不完），VL 看图是**归纳**逻辑（任何产品都能理解）。

### 4.2 第一层：角色 + gpt-image-2 执行规则

```
你是电商主图策划专家，同时精通图片生成模型（gpt-image-2）的提示词编写。

你的任务：根据用户上传的产品参考图和文字描述，策划一整套电商主图方案，
并为每张图直接写出 gpt-image-2 的执行指令（prompt）。

## 你的工作方式

### 第一步：看懂产品
用户会提供：产品名称（必填）、参考图（必填）、核心卖点/价格/目标用户（选填）。
你需要：
- 观察参考图，识别核心外观特征（颜色、形状、材质、LOGO、尺寸感）
- 用户给了卖点 → 直接采用，围绕它策划
- 用户没给卖点 → 根据图片+产品名称+品类常识推断，在 product_insight 中标注"推断"
- 用户给了价格/促销信息 → 安排一张促销图（带价格/满减/赠品的图）
- 用户没给价格 → 不做促销图，但其他营销图（钩子图、卖点图、场景图）正常生成，用卖点或场景图替补促销图的位置
- 产品名称必须准确用于文案中，不要自己改名

### 第二步：策划方案
根据注入的平台规则，策划这套图的整体方案：
- 如果用户上传了风格参考图，先分析参考图的设计语言：
  - 色调和配色（暖色/冷色/高饱和/莫兰迪）
  - 排版方式（文字位置/产品位置/留白比例）
  - 氛围感（专业/活泼/种草/高端）
  - 设计手法（渐变背景/场景融入/纯色/几何装饰）
  - 将这些风格元素应用到你的方案中。风格参考 ≠ 照抄，提取设计语言适配当前产品
- 如果没有风格参考图，根据平台风格特征 + 产品品类自主判断
- 确定每张图的营销角色（你自己决定，不是固定的）
- 确定整套图的视觉统一性（色调、风格、氛围关键词）
- 为每张图构思具体画面

### 第三步：写执行 prompt
为每张图写出 gpt-image-2 能直接执行的 prompt。
这是最关键的一步——你写的每条 prompt 会直接发送给 gpt-image-2 模型执行生图。

## gpt-image-2 的 prompt 写法规则（必须严格遵循）

### 核心原则
gpt-image-2 的 image-to-image 模式已由系统自动设置（不需要你在 prompt 中触发）。
你的 prompt 需要做的是：**告诉模型保留什么、改变什么、最终画面是什么样**。

### 结构顺序
每条 prompt 按此顺序组织：
  ① Preserve 指令（保留什么） → ② Scene/Background（改变什么） → ③ Product placement（产品在画面中的位置） → ④ Text content（文字内容） → ⑤ Style/Mood（风格氛围） → ⑥ Constraints（约束/禁止项）

### 语言
- 主体描述用英文（gpt-image-2 对英文理解最好）
- 需要渲染的中文文案用引号包裹（如 title "一盒搞定"）

### 必须做的
- 每条 prompt 开头明确 Preserve/Change 结构：
  "Preserve the product geometry, colors, labels, and all visual details from the reference image. Change only the background and add text overlay."
- 多张参考图时用 Image 索引标注角色：
  "Image 1 (product photo): preserve entirely. Image 2 (style reference): apply its color palette and layout."
- 前 50 个词放最重要的信息（保留指令、风格、主体）
- 用具体视觉描述："dark gradient background, soft rim lighting highlighting metallic texture"
- 中文文案用引号包裹 + 指定字号/颜色/位置
- 加 "high-fidelity Chinese typography, crisp text rendering" 锚定文字渲染质量
- 约束放最后："No watermark, no logo, no extra text unless specified."
- 标题控制在 12 字以内，副标题 15 字以内

### 禁止做的
- 禁止用精确摄影参数："色温5200K""F2.8光圈""双侧45度柔光箱"（模型会松散解释，用自然描述代替：如 "soft depth-of-field blur" 代替 "f/2.8"）
- 禁止用抽象形容："高端大气上档次""简约而不简单"（用具体视觉描述代替）
- 禁止用结构化标签：不要写 "【背景】【光线】【构图】"
- 禁止写长段落——每条 prompt 控制在 80-150 词
- 禁止在白底图中加任何文字或装饰
- 禁止用模糊的保留指令："preserve entirely"（太模糊，要具体说保留什么：geometry, colors, labels, texture）
```

### 4.3 第二层：平台规则（从配置文件注入）

平台规则**不硬编码在 prompt 里**，从 `platform_rules.yaml` 配置文件读取后注入 system prompt。

配置文件结构：
```yaml
# backend/services/agent/image/platform_rules.yaml
# 最后更新：2026-05-10
# 更新频率：每月检查一次各平台卖家中心的规则变化
#
# 设计原则：
#   - default_size 是平台推荐尺寸，实际尺寸由用户在前端选择
#   - styles 描述平台的风格"光谱"，不写死每张图的角色
#   - hard_rules 是平台的硬性规则（违反会被平台驳回/限流）
#   - 千问根据 styles + 实际产品 自主决定每张图的角色和内容
#   - 不要在这里写死"第1张是钩子图第2张是卖点图"——那是千问的工作

taobao:
  label: "淘宝/天猫"
  main_image:
    count: 5
    default_size: "800×800"
    aspect_ratio: "1:1"
  styles:
    # 描述这个平台上什么风格效果好，千问根据产品自己选
    - "高饱和、强对比、卖点大字直给——手机缩略图0.5秒抓眼球"
    - "年轻品类偏精致生活感，服装强调上身效果和面料质感"
    - "食品类暖色调、食欲感、堆满画面比留白好"
    - "3C/家电偏理性，参数+品质感+认证背书"
  avoid:
    - "莫兰迪色系（在淘宝缩略图中不够突出）"
    - "过度留白（信息密度太低，在信息流中被淹没）"
    - "纯艺术感（买家看不懂产品是什么）"
  hard_rules:
    - "最后一张必须纯白底：纯白背景，产品居中，无文字无水印无装饰（平台强制要求）"
    - "2026新规：SKU规格图直接出搜索结果，规格图质量影响搜索曝光"
  detail_page:
    count: "8-10张"
    note: "竖版信息图"

jd:
  label: "京东"
  main_image:
    count: 5
    default_size: "800×800"
    aspect_ratio: "1:1"
  styles:
    - "品质感和专业度要求最高——京东用户偏理性消费"
    - "3C/家电：参数硬核展示、工艺细节、认证背书"
    - "日用/食品：品质感+生活场景，比淘宝更克制"
    - "服装：模特图优先，面料质感+版型展示"
  hard_rules:
    - "首图必须纯白底居中（京东强制要求，否则不予展示）"
    - "营销面积≤35%（图片中文字/促销信息占比不能超过35%）"
    - "禁止出现其他平台信息、联系方式、外链、二维码"
  detail_page:
    count: "8-10张"
    note: "PC端宽790px"

pdd:
  label: "拼多多"
  main_image:
    count: 5
    default_size: "800×800"
    aspect_ratio: "1:1"
  styles:
    # 拼多多风格跨度极大，完全取决于品类，不能一刀切
    - "传统日用/百货：白底+产品居中+性价比大字直给，简单粗暴"
    - "食品/生鲜：鲜艳色彩、堆满画面、食欲感爆棚、价格醒目"
    - "年轻/文创/潮玩：网感风格——强对比、有梗、表情包式视觉冲击"
    - "美妆/个护：种草风——精致真实、生活化、像小红书博主分享"
    - "家居/收纳：场景化展示>棚拍——放在真实环境中让用户代入"
    - "核心共性：价格敏感、信息直给、不绕弯子"
  hard_rules:
    - "无水印、无边框、无拉伸变形"
    - "商品占画面70%以上（商品必须是视觉主体）"
  detail_page:
    count: "6-8张"

douyin:
  label: "抖音"
  main_image:
    count: 3
    default_size: "1024×1536"
    aspect_ratio: "3:4"
  styles:
    - "竖图3:4——适配手机全屏浏览"
    - "强视觉冲击、即时感——在快速滑动的信息流中必须'跳出来'"
    - "素人感>精修感——过度精修反而让用户觉得是广告"
    - "使用前后对比效果好"
    - "年轻化、情绪化表达"
  hard_rules: []
  detail_page:
    count: 0
    note: "抖音以视频为主，无传统详情页"

xiaohongshu:
  label: "小红书"
  main_image:
    count: 9
    default_size: "1024×1024"
    aspect_ratio: "1:1 或 3:4"
  styles:
    - "种草氛围——像博主真实分享，不像品牌广告"
    - "精致但不过度精修——有生活感和真实感"
    - "色调统一和谐——莫兰迪色系/奶茶色系/统一滤镜感"
    - "场景化>棚拍——产品在生活中的样子比纯产品图更吸引"
    - "第一人称视角效果好——桌面俯拍、手持展示、使用过程"
    - "美妆：质地特写、上脸效果、色号对比"
    - "家居：房间场景融入、氛围感"
  hard_rules: []
  detail_page:
    count: 0
    note: "小红书无传统详情页，靠笔记图组"

ali1688:
  label: "1688"
  main_image:
    count: 5
    default_size: "800×800"
    aspect_ratio: "1:1"
  styles:
    - "B端批发视角——买家是经销商/采购，不是终端消费者"
    - "信息密集、参数详尽——材质、规格、工艺、起批量都要展示"
    - "多角度全方位展示产品——正面/侧面/背面/内部结构"
    - "包装和物流方式展示——采购方关心怎么发货"
    - "品质管控证据——工厂实拍、认证证书、质检报告"
  hard_rules:
    - "强调供货能力和品质管控（B端核心关注点）"
  detail_page:
    count: "10-15张"
    note: "B端详情页偏长，重参数和供货信息"
```

注入方式：根据用户选择的平台，读取对应配置，拼接进 system prompt：

```python
def _inject_platform_rules(platform: str) -> str:
    """从配置文件读取平台规则，格式化为 system prompt 片段。"""
    rules = load_platform_rules(platform)

    parts = [f"## 目标平台：{rules['label']}"]

    # 主图基本信息
    mi = rules['main_image']
    parts.append(f"- 主图数量：{mi['count']}张")
    parts.append(f"- 默认尺寸：{mi['default_size']}（{mi['aspect_ratio']}）")

    # 风格特征（描述光谱，不写死角色）
    if rules.get('styles'):
        parts.append("\n### 该平台的风格特征（参考，根据实际产品灵活选择）")
        for s in rules['styles']:
            parts.append(f"- {s}")

    # 避免事项
    if rules.get('avoid'):
        parts.append("\n### 该平台应避免")
        for a in rules['avoid']:
            parts.append(f"- {a}")

    # 硬性规则（必须遵守）
    if rules.get('hard_rules'):
        parts.append("\n### 硬性规则（违反会被平台驳回，必须遵守）")
        for r in rules['hard_rules']:
            parts.append(f"- ⚠️ {r}")

    # 详情页
    dp = rules.get('detail_page', {})
    if dp.get('count') and dp['count'] != 0:
        parts.append(f"\n### 详情页：{dp['count']}")
        if dp.get('note'):
            parts.append(f"- {dp['note']}")
    else:
        parts.append(f"\n### 详情页：{dp.get('note', '无')}")

    return "\n".join(parts)
```

### 4.4 第三层：输出格式约束

```
## 输出格式（严格 JSON，必须可被 json.loads 解析）

```json
{
  "product_insight": "一句话总结你对产品的理解（中文）",
  "visual_strategy": "一句话总结视觉策略：色调+风格+氛围（中文）",
  "images": [
    {
      "role": "钩子图",
      "purpose": "核心卖点直给，0.5秒抓住注意力（中文，给用户看）",
      "title": "一盒搞定（中文，给用户编辑用）",
      "subtitle": "56色分类收纳（中文，给用户编辑用，可为空）",
      "prompt": "Based on the uploaded product reference image, create an e-commerce hero image. ...(英文为主，给 gpt-image-2 执行)",
      "aspect_ratio": "1:1",
      "has_text": true,
      "image_type": "marketing"
    }
  ]
}
```

### 字段说明
- **role**：中文，这张图的营销角色，给用户看
- **purpose**：中文，这张图的目的，给用户看
- **title**：中文，画面上的主标题文案，用户可编辑后同步回 prompt
- **subtitle**：中文，画面上的副标题文案，可为空（白底图/纯场景图无文字）
- **prompt**：gpt-image-2 的执行指令。英文为主，中文文案用「」包裹。这是最终发给模型的
- **aspect_ratio**：宽高比，主图"1:1"，竖图"3:4"
- **has_text**：布尔值，这张图是否包含文字。true 时 quality 设为 high
- **image_type**：图片类型，枚举值：
  - "marketing"：营销图（有文字+设计感背景）
  - "scene"：场景图（产品在生活场景中）
  - "white_bg"：纯白底图（无文字无装饰）
  - "detail"：细节特写图（局部放大）

### prompt 写法示例（供你参考，不要照抄，根据实际产品灵活变化）

每条示例都遵循：Preserve → Scene → Product → Text → Style → Constraints

**钩子图（营销主图，有文字）**：
```
Preserve the product geometry, colors, texture, and all visual details from the reference image (Image 1). Change only the background and add text.
Vibrant coral-to-orange gradient background. Product centered, occupying 55% of the frame, crisp and detailed with soft studio lighting from the upper left.
Bold title "一盒搞定" in white, 56pt bold sans-serif, top-center area.
Subtitle "56色分类收纳" in light gray, 18pt, directly below the title.
Style: energetic, eye-catching, high saturation, designed for mobile thumbnail browsing. Square 1:1 format.
High-fidelity Chinese typography, crisp text rendering. No watermark, no logo, no extra text.
```

**白底图（纯产品，无文字无装饰）**：
```
Preserve the product geometry, colors, labels, and every surface detail from the reference image (Image 1). Change only the background.
Pure white background (#FFFFFF), absolutely no shadows, no gradients, no reflections.
Product centered, front-facing, occupying 60% of the frame, even spacing on all sides.
Even, neutral studio lighting from all directions.
No text, no watermark, no decorative elements whatsoever.
```

**场景图（产品融入生活场景）**：
```
Preserve the product geometry, colors, and all visual details from the reference image (Image 1). Place it into a new scene.
Scene: a clean wooden desk in a bright home studio, next to scattered colorful beads and a small green plant. Warm natural window light from the left, soft depth-of-field blur on the background.
The product is the clear hero of the scene, occupying 30% of the frame.
Small caption "手工DIY必备" in 24pt dark gray, bottom-right corner with subtle translucent white backing.
Atmosphere: warm, creative, inviting. Photorealistic lifestyle photography feel.
High-fidelity Chinese typography. No watermark, no extra text beyond the caption.
```

**促销图（带价格/促销信息，有文字）**：
```
Preserve the product geometry, colors, and all visual details from the reference image (Image 1). Change only the background and add promotional text.
Deep red solid background with subtle geometric accent shapes in gold.
Product on the left side, occupying 35% of the frame.
Right side text area:
  Main title "限时特惠" in 72pt bold white sans-serif.
  Subtitle "前100名下单送色卡" in 28pt light gold.
  Price "¥39.9" in 60pt bold yellow.
Bold, high-contrast promotional style. Square 1:1 format.
High-fidelity Chinese typography, crisp text rendering. No watermark, no extra elements.
```

**有风格参考图时的钩子图**：
```
Preserve the product geometry, colors, and all visual details from Image 1 (product photo). Apply the visual style from Image 2 (style reference): adopt its color palette, layout approach, and overall atmosphere.
Change only the background and add text. Product centered, occupying 50% of the frame.
Bold title "一盒搞定" in white, 56pt bold, top-center. Subtitle "56色分类收纳" in 18pt light gray below.
High-fidelity Chinese typography. Square 1:1 format. No watermark, no logo.
```
```

### 4.5 品类启发（注入 system prompt 尾部）

不用品类模板穷举。千问 VL 看图即知品类。但可以注入**品类营销要点**作为启发：

```
## 品类营销要点（参考，不要死板套用）

根据你识别出的产品品类，参考以下营销策略要点：

- **服装**：面料纹理需可见，版型和剪裁是重点，场景图需展示穿搭效果
- **食品**：色泽鲜亮有食欲感，暖色调为主，可用食材散落/水珠增加新鲜感
- **3C数码**：工艺细节/接口清晰，深色背景展示科技感，参数是核心卖点
- **美妆**：质地特写（膏体/液体流动），标签文字清晰可读，渐变色背景（粉/紫/裸色）
- **家居**：融入房间场景展示搭配效果，材质纹理（木纹/布纹）是重点
- **珠宝**：微距特写，控制金属反光和宝石折射，深色背景（丝绒黑/酒红）
- **母婴**：明亮温暖柔和，色彩鲜艳准确，传递安全温馨感
- **宠物**：展示宠物使用效果，温馨家庭感，宠物出镜增加吸引力
- **农产品**：原生态感，产地元素，水润新鲜感，竹编/麻布/牛皮纸背景

以上仅为启发，你应该根据实际看到的产品灵活调整，不要机械套用。
对于不在以上列表中的品类，你完全有能力自主判断营销策略。
```

---

## 五、千问输出解析

### 5.1 解析策略

千问输出严格 JSON → 后端 `json.loads` 直接解析。

**兜底策略**：如果 JSON 解析失败（千问偶尔在 JSON 前后加多余文字），用正则提取 `{...}` 部分重试。

```python
import json
import re

def parse_design_plan(content: str) -> dict:
    """解析千问输出的设计方案 JSON。"""
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 兜底：提取最大的 JSON 块
    match = re.search(r'\{[\s\S]*\}', content)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 最终兜底：整段作为单张图的 prompt
    return {
        "product_insight": "",
        "visual_strategy": "",
        "images": [{
            "role": "主图",
            "purpose": "产品展示",
            "title": "",
            "subtitle": "",
            "prompt": content.strip(),
            "aspect_ratio": "1:1",
            "has_text": False,
            "image_type": "marketing",
        }],
    }
```

### 5.2 用户编辑文案同步

用户在前端编辑 `title` / `subtitle` 后，需要同步更新对应图的 `prompt`。

**方案**：prompt 中的中文文案用「」包裹，编辑后用正则替换：

```python
def sync_text_to_prompt(prompt: str, new_title: str, new_subtitle: str) -> str:
    """将用户编辑的文案同步回 prompt 中的「」标记位置。"""
    # 按顺序替换：第一个「」替换为 title，第二个替换为 subtitle
    parts = re.split(r'「[^」]*」', prompt)
    if len(parts) >= 3 and new_title and new_subtitle:
        return f"{parts[0]}「{new_title}」{parts[1]}「{new_subtitle}」{''.join(parts[2:])}"
    elif len(parts) >= 2 and new_title:
        return f"{parts[0]}「{new_title}」{''.join(parts[1:])}"
    return prompt
```

---

## 六、图片生成阶段

### 6.1 强制 image-to-image

EcomImageHandler 修改：
- 用户上传的参考图**必须**传递到每张图的生成请求
- 模型固定使用 `gpt-image-2-image-to-image`
- 参考图标注角色：prompt 以 `"Based on the uploaded product reference image,"` 开头

### 6.2 两种参考图的传递

用户上传的图片分两组，在调用 gpt-image-2 时按角色标注传入：

```
产品图（必填，1-5张）      风格参考图（选填，0-3张）
     ↓                           ↓
Image 1: product photo       Image N: style reference
  (preserve entirely)          (apply this visual style)
Image 2: product angle 2
  (additional reference)
```

**传递给 gpt-image-2 的 input_urls 数组**：
```python
def build_reference_images(
    product_urls: list[str],     # 产品图 URLs
    style_ref_urls: list[str],   # 风格参考图 URLs
) -> list[str]:
    """合并两种参考图，产品图在前，风格参考图在后。"""
    return product_urls + style_ref_urls
    # gpt-image-2 通过 prompt 中的标注区分角色
```

**prompt 中的标注方式**（千问在写 prompt 时需要包含）：

有风格参考图时，千问的 prompt 必须包含 Image 索引标注：
```
Preserve the product geometry, colors, and all details from Image 1-3 (product photos).
Apply the visual style from Image 4-5 (style references): adopt their color palette,
layout approach, and overall atmosphere. Change only the background and add text.
...
```

无风格参考图时：
```
Preserve the product geometry, colors, and all details from the reference image (Image 1).
Change only the background and add text.
...
```

**每种生成图使用的参考图组合**：

| 图片类型 | 产品图 | 风格参考图 | 说明 |
|---------|--------|-----------|------|
| 营销图 | 全部 | 全部 | 产品外观+风格都要参照 |
| 场景图 | 全部 | 全部 | 同上 |
| 白底图 | 仅第1张 | 不传 | 白底图不需要风格参照，减少干扰 |
| 细节图 | 全部 | 全部 | 细节特写也参照风格 |

### 6.3 千问如何分析风格参考图

千问 VL 收到风格参考图后，需要在 JSON 输出的 `visual_strategy` 中说明参照了什么：

```json
{
  "visual_strategy": "参照用户提供的风格参考图：采用高饱和橙红渐变背景、大字居中排版、产品占画面50%的构图方式",
  ...
}
```

千问在 system prompt 中的指引（已包含在第一层角色定义中）：

```
### 第二步：策划方案
...
- 如果用户上传了风格参考图，先分析参考图的设计元素：
  - 色调和配色方案（暖色/冷色/高饱和/莫兰迪）
  - 排版方式（文字位置/产品位置/留白比例）
  - 氛围感（专业/活泼/种草/高端）
  - 设计手法（渐变背景/场景融入/纯色背景/几何装饰）
- 将这些风格元素应用到你的方案中，但产品外观必须保持参考图中的样子
- 风格参考图 ≠ 照抄。提取其设计语言，适配到当前产品上
```

### 6.3 quality 参数策略

根据 `has_text` 字段自动设置：
- `has_text: true`（营销图/促销图）→ `quality="high"`
- `has_text: false`（白底图/场景图/细节图）→ `quality="medium"`（节省成本，无文字时 medium 已足够）

### 6.4 生成顺序与用户体验

15 张图（5主图+10详情页）按 300ms 间隔逐张调用 = 用户等待 2-7 分钟。

优化方案：
1. **主图优先**：先生成 5 张主图，全部完成后再生成详情页
2. **逐张推送**：每完成一张立即 WS 推送前端展示
3. **进度提示**：推送时附带 `"正在生成第 3/5 张：促销图"` 的进度信息
4. **详情页确认**：主图全部生成后，用户确认满意再触发详情页生成（避免浪费积分）

---

## 七、详情页策略

### 7.1 AI 生成类 vs 模板合成类

调研结论：并非所有详情页类型都适合 gpt-image-2。按可行性分两类：

| 类型 | AI 生成？ | 原因 |
|------|----------|------|
| D1 首屏海报 | ✅ 是 | 产品+背景+大标题，gpt-image-2 擅长 |
| D2-D3 卖点图 | ✅ 是 | 产品+文字说明，布局可能飘但可接受 |
| D4 产品实拍 | ✅ 是 | 多角度棚拍，gpt-image-2 核心能力 |
| D5 细节特写 | ✅ 是 | 局部放大+材质渲染，gpt-image-2 擅长 |
| D6 使用场景 | ✅ 是 | 产品融入场景，gpt-image-2 核心能力 |
| D7 规格参数 | ❌ 否 | 表格/标注线/精确数据排版，AI 不可控 → 模板合成 |
| D8 包装清单 | ❌ 否 | 多物品平铺排列+标注，AI 不可控 → 需用户拍实物 |
| D9 售后保障 | ❌ 否 | 图标+文字列表，设计模板场景 → 模板合成 |

### 7.2 v2 范围

**v2 只做 AI 生成类**（D1-D6）。模板合成类（D7-D9）作为后续版本的独立项目。

千问在策划详情页时，只需要输出 D1-D6 的 prompt，D7-D9 不输出。

### 7.3 详情页 prompt 要点

与主图的区别：
- 默认 aspect_ratio 为 `"3:4"` 竖版，但实际尺寸取决于用户选择：
  - 用户选 800×800 → 详情页也是 800×800（1:1）
  - 用户选 1024×1536 → 详情页是 1024×1536（3:4）
  - 用户选自定义 → 按用户指定
- 文字密度更高（卖点图需要标题+说明文字）
- 产品占比更灵活（首屏海报 40-50%，场景图 25-30%）

千问输出的 JSON 中，详情页图片的 `image_type` 标记为对应类型（`"detail_hero"` / `"detail_selling_point"` / `"detail_scene"` 等），前端据此分组展示。

---

## 八、前端变化

### 8.1 AI 增强按钮的交互变化

现在：点击 → 返回增强后的纯文本提示词 → 用户编辑文本 → 发送
改为：点击 → 返回 JSON 结构化方案 → 前端渲染方案卡片 → 用户编辑文案 → 发送

### 8.2 方案卡片展示

```
┌─────────────────────────────────────────────┐
│ 📋 产品理解：56色拼豆收纳盒，DIY手工爱好者必备  │
│ 🎨 视觉策略：暖橙色调，活泼创意感，高饱和直给    │
├─────────────────────────────────────────────┤
│                                             │
│ ⚙️ 生成设置                                 │
│ ┌─────────────────────────────────────┐     │
│ │ 生成范围：                            │     │
│ │   [✓] 主图（5张）  [✓] 详情页（6张）  │     │
│ │                                     │     │
│ │ 图片尺寸：                            │     │
│ │   ● 800×800   ○ 1024×1024            │     │
│ │   ○ 1024×1536(竖版)  ○ 自定义        │     │
│ │   ℹ️ 淘宝主图标准尺寸 800×800         │     │
│ └─────────────────────────────────────┘     │
│                                             │
│ 主图（5张）                                  │
│                                             │
│ ┌─────────────────────────────────────┐     │
│ │ 第1张 · 钩子图                       │     │
│ │ 目的：核心卖点直给，抓住注意力         │     │
│ │ 主标题：[一盒搞定_________]  ← 可编辑 │     │
│ │ 副标题：[56色分类收纳______]  ← 可编辑 │     │
│ │ ⚠️ 含中文文字（渲染准确率~99%）       │     │
│ └─────────────────────────────────────┘     │
│                                             │
│ ┌─────────────────────────────────────┐     │
│ │ 第2张 · 卖点图                       │     │
│ │ 目的：展示大容量差异化优势             │     │
│ │ 主标题：[4层大容量________]  ← 可编辑 │     │
│ │ 副标题：[装下200+瓶_______]  ← 可编辑 │     │
│ └─────────────────────────────────────┘     │
│                                             │
│ ┌─────────────────────────────────────┐     │
│ │ ...                                 │     │
│ └─────────────────────────────────────┘     │
│                                             │
│ ┌─────────────────────────────────────┐     │
│ │ 第5张 · 白底图（自动生成，无需编辑）   │     │
│ └─────────────────────────────────────┘     │
│                                             │
│ ── 详情页（确认主图满意后生成）──             │
│ [✓] 生成详情页（6张）   [预估48积分]         │
│                                             │
├─────────────────────────────────────────────┤
│          [确认生成 · 主图预估40积分]           │
└─────────────────────────────────────────────┘
```

### 8.3 编辑同步机制

用户编辑卡片中的 `title` / `subtitle` 输入框 → 触发 `sync_text_to_prompt()`：
1. 更新 `imageTaskMeta[i].title` / `imageTaskMeta[i].subtitle`
2. 同步更新 `imageTaskMeta[i].prompt` 中「」包裹的中文文案

前端维护完整的 `imageTaskMeta[]` 数组，提交时整体发送给后端。

---

## 九、与现有架构的关系

### 不变的部分
- ImageHandler 的批次生成逻辑（循环调用 + WS 逐张推送）
- KIE adapter 的 gpt-image-2 调用（image_adapter.py）
- WebSocket image_partial_update 协议
- 前端占位符和图片渲染逻辑
- 积分锁定/确认/退款链路
- OSS 上传和 CDN 链路

### 需要改的部分

| 文件 | 改动 | 说明 |
|------|------|------|
| `prompts.py` | 重写 | 三层 system prompt（角色+执行规则+品类启发），删除旧的四层体系 |
| 新增 `platform_rules.yaml` | 新建 | 平台规则配置文件，支持动态更新 |
| `prompt_builder.py` | 重写 | 读取配置文件 + 三层 prompt 组装，删除旧的品类检测/风格矩阵 |
| `image_ecom.py` | 重写 | JSON 解析替代 regex，`sync_text_to_prompt()` 文案同步，请求模型适配新字段 |
| ~~`prompt_translator.py`~~ | ~~不需要~~ | v2.2 删除翻译层，千问直接输出可执行 prompt |
| `ecom_image_handler.py` | 修改 | 强制 image-to-image + quality 按 has_text 分级 + 进度推送 |
| `image_handler.py` | 小改 | batch_prompts 支持 quality 参数 |
| 前端 `InputArea.tsx` | 重写 | 产品信息表单（必填/选填）+ 方案卡片展示 + 文案编辑 + 同步更新 prompt |
| 前端 messageSender | 适配 | imageTaskMeta 新格式（含 prompt/title/subtitle/has_text 等字段） |
| 前端 `EnhancePromptRequest` | 适配 | 新增 product_name(必填)、selling_points、price_info、target_user、image_size 字段 |

---

## 十、实施路径

### Phase 0：手动验证（1天）

**目的**：用真实产品图验证 gpt-image-2 的 image-to-image 能力。

验证清单：
1. **中文文字渲染**：分别测试 4字/8字/12字/15字标题，统计准确率
2. **产品保留度**：测试 3 个品类（硬质产品/柔性产品/复杂结构产品），对比参考图和生成图的保留度
3. **营销图效果**：用手写的英文 prompt（遵循官方结构），生成5张淘宝主图，评估是否达到可用水平
4. **quality 对比**：对比 medium 和 high 在文字渲染上的差异
5. **白底图效果**：验证纯白底+产品保留的效果

**通过标准**：
- 中文 4-12 字标题准确率 ≥ 95%
- 产品保留度主观评分 ≥ 7/10（硬质产品）
- 5张主图中 ≥ 3张可直接使用，其余可微调后使用

### Phase 1：后端提示词体系重建（2天）

- 新建 `platform_rules.yaml` 配置文件
- 重写 `prompts.py`：三层 system prompt
- 重写 `prompt_builder.py`：读取配置 + 组装 + 注入
- 重写 `image_ecom.py`：JSON 解析 + `sync_text_to_prompt()`
- 删除旧的四层体系（CATEGORY_TEMPLATES / STYLE_PRESETS / CATEGORY_PLATFORM_STYLE）

### Phase 2：图生图链路强化（1天）

- EcomImageHandler 强制 image-to-image
- quality 按 has_text 自动分级
- 多参考图标注角色
- 生成进度推送（"第 3/5 张：促销图"）

### Phase 3：前端方案卡片（2天）

- enhance API 返回结构化 JSON（不是纯文本）
- 前端渲染方案卡片 + 文案编辑输入框
- 编辑同步更新 imageTaskMeta 中的 prompt
- 详情页开关（确认主图后再生成）

### Phase 4：多品类多平台测试（1天）

- 品类测试：收纳/服装/食品/3C/美妆/家居，每个品类 5 张主图
- 平台测试：淘宝/京东/拼多多，验证规则适配
- 根据实际效果迭代 system prompt 的品类启发和平台规则

---

## 十一、风险清单

| 风险 | 级别 | 缓解方案 |
|------|------|---------|
| 中文文字渲染偶发错字（~5%） | 中 | has_text 标记 + 前端提示"文字可能需手动微调" + quality=high |
| 柔性产品（服装/食品）保留度低 | 中 | prompt 中增加关键特征描述 + 允许用户上传多角度参考图 |
| 千问 VL 输出非法 JSON | 低 | 三层兜底解析（直接→正则提取→整段兜底） |
| 平台规则过期 | 低 | 配置文件 + 月度检查更新 + 日志监控用户反馈 |
| 15张图生成耗时过长 | 中 | 主图/详情页分批 + 逐张推送 + 详情页用户确认后再生 |
| 服装品类缺模特 | 高 | v2 告知用户"服装建议上传模特图"，v3 规划模特生成能力 |

---

## 十二、与 v1 / v2.1 的核心区别

| 维度 | v1（当前） | v2.1（上版） | v2.2（本版） |
|------|-----------|-------------|-------------|
| AI 角色 | 摄影师/视觉总监 | 电商营销策划师 | **策划师 + gpt-image-2 执行者**（二合一） |
| 输出内容 | 中文拍摄指令 | 中文营销方案 → 翻译 → prompt | **直接输出 gpt-image-2 可执行 prompt** |
| 翻译层 | 无（直接用中文） | 有（prompt_translator.py） | **无（一步到位）** |
| prompt 语言 | 中文 | 中文方案 → 英文 prompt | **英文为主 + 中文文案用「」** |
| prompt 格式 | 结构化参数 | 中文自由文本 | **对齐 OpenAI 官方结构** |
| 品类适配 | 12个模板穷举 | AI 自主判断（无参考） | **VL 看图理解 + 品类启发注入** |
| 平台规则 | 硬编码在 prompt | 硬编码在 prompt | **配置文件，支持更新** |
| 输出格式 | 正则解析 numbered list | 正则解析中文方案 | **JSON 直接解析** |
| 文案编辑 | 编辑纯文本（无效） | 编辑方案字段（需翻译） | **编辑「」文案 → 实时同步 prompt** |
| 详情页 | 无 | D1-D9 全做 | **D1-D6 AI 生成，D7-D9 后续版本** |
| 生成模式 | text-to-image 为主 | image-to-image | **image-to-image + quality 分级** |

---

## 附录 A：调研来源

| 来源 | 关键发现 |
|------|---------|
| [OpenAI 官方 Prompting Guide](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide) | prompt 结构 Scene→Subject→Details→Constraints；前50词权重最高；多图标注角色 |
| [fal.ai GPT Image 2 Guide](https://fal.ai/learn/tools/prompting-gpt-image-2) | "像给摄影师写 brief"而不是关键词列表；edit 模式 Preserve/Change 结构 |
| [中文渲染深度分析](https://help.apiyi.com/en/why-gpt-image-2-more-popular-than-1-5-chinese-character-rendering-en.html) | gpt-image-2 中文 ~99%（vs 1.5 的 ~80%）；4-12字最稳定；85%可直接发布 |
| [电商实战5步法](https://help.apiyi.com/en/gpt-image-2-ecommerce-product-image-from-long-text-to-elegant-design-en.html) | 3层信息架构→专用模板→批量变体；quality=high 用于文字；1024×1024 够用 |
| [WaveSpeed API Guide](https://wavespeed.ai/blog/posts/gpt-image-2-api-guide/) | 最多10张参考图；mask 支持精确区域编辑；n 参数是同 prompt 多变体 |
| [Coze 电商商拍工作流](https://www.53ai.com/news/zhinengyingxiao/2024081181493.html) | 背景替换节点（GENERAL/ROOM/COSMETIC）；三种方式放一个流出三张图 |
| [Coze 穿搭 Agent](https://blog.csdn.net/m0_53539063/article/details/149982676) | 双工作流架构：LLM 分析图→生成 prompt 数组→循环节点逐张生图 |
| [GitHub Coze 200+ 工作流](https://github.com/Hammer1/cozeworkflows) | X257 一键生成电商主图：产品图→Flux→主图 |
| [Felo Product Prompts](https://felo.ai/blog/gpt-image-2-prompts/) | "short copy still works best"；迭代微调优于大改 |
