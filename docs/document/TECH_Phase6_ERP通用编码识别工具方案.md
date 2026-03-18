# 新增 ERP 通用编码识别工具 (erp_identify)

## Context

AI 拿到裸值编码后不知道是什么类型，直接猜参数查询。套件编码浪费 3 轮查询失败，订单号 order_id/system_id 经常搞反。

**设计原则**：
1. **按最差输入设计**：用户只丢一个裸值，不说明类型
2. **关联参数齐全**：识别时把下游查询需要的所有参数都拿到
3. **现有宽泛查询不动**：新工具是正路，宽泛查询是兜底
4. **通用适配器模式**：快麦是第一个实现，架构支持任何 ERP 接入复用

## 架构（通用适配器）

```
┌─ 工具层（通用，所有ERP共享）─────────────────────┐
│  erp_tools.py    → erp_identify 工具定义（不变）  │
│  tool_executor.py → handler 委托给当前 ERP 实现   │
└──────────────────────────────────────────────────┘
         ↓ 统一接口: identify_code(client, code) -> str
┌─ 适配层（每个 ERP 各自实现）────────────────────┐
│  services/kuaimai/code_identifier.py    ← 快麦   │
│  services/[other_erp]/code_identifier.py ← 未来  │
└──────────────────────────────────────────────────┘
```

**统一接口约定**：
- 输入：`(client, code: str)` — 任何 ERP client + 裸值编码
- 输出：结构化文本，固定格式（编码类型 + 关联参数）
- 每个 ERP 用自己的 API 实现识别逻辑，AI 端零改动

## 识别范围（3类 + 回退机制）

| 类型 | 混淆对 | 识别方式 | 返回的关联参数 |
|------|--------|---------|-------------|
| 商品编码 | outer_id ↔ sku_outer_id ↔ 套件 | API调用 | outer_id, sku_outer_id, item_id, sku_id, type, name, SKU列表 |
| 订单号 | order_id ↔ system_id | 规则匹配+API验证 | order_id, system_id, platform, buyer, status |
| 条码 | barcode vs 商品编码 | 格式判断(13位69开头)+API | barcode, outer_id, name |

> 仓库/供应商编码不纳入：通常有上下文（"查XX仓库"），不会作为裸值出现，AI 自行判断即可。

## 识别流程（含回退）

```
输入: 裸值 code
  ↓
Step 0: 输入校验
  - strip空格
  - 空字符串 → 返回错误
  - 含逗号 → 提示"请逐个识别，不支持批量"
  ↓
Step 1: 格式预判
  - 13位且69开头 → 条码分支
  - P+18位 → 小红书 order_id（格式唯一，不需回退）
  - 日期-数字串(如260305-xxx) → 拼多多 order_id（格式唯一）
  - 纯数字18位 → 先试订单分支（淘宝）
  - 纯数字19位 → 先试订单分支（抖音/1688）
  - 纯数字16位 → 先试订单分支（京东/快手/系统单号）
  - 其他（含字母） → 商品编码分支
  ↓
Step 2: 按分支识别（每个分支失败后回退到商品分支）

【商品编码分支】— 默认分支 & 最终回退
  a. item.single.get(outerId=code)
     → 找到 → 返回主编码信息(type, name, item_id, SKU列表)
  b. 没找到 → erp.item.single.sku.get(skuOuterId=code)
     → 找到 → 返回SKU信息(规格, sku_id, 对应主编码)
  c. 都没找到 → "编码不存在"

【订单号分支】
  a. 格式已明确(18/19/P+18/日期串) → erp.trade.list.query(tid=code)
     → 找到 → 返回订单信息(order_id, system_id, buyer, status)
  b. 16位不确定 → 先试tid，没找到试sid
     → 找到 → 返回订单信息
  c. ⚠ 订单也没找到 → **回退到商品编码分支**（纯数字也可能是商品编码）

【条码分支】
  a. multicode_query(code=barcode)
     → 找到 → 返回条码信息(barcode, outer_id, name)
  b. ⚠ 没找到 → **回退到商品编码分支**

⚠ 每个API调用都有 try-except，异常时跳到下一步，不中断流程
```

## 边缘情况处理

| 场景 | 处理 |
|------|------|
| 纯数字商品编码(如"8001") | 位数不匹配订单格式 → 直接进商品分支 |
| 16位纯数字商品编码 | 先走订单分支(找不到) → 回退商品分支(找到) |
| 订单号在ERP中不存在 | 回退商品分支 → 都没有 → "未识别" |
| API超时/异常 | try-except跳过，继续下一步 |
| 空字符串/空格 | 返回"请提供有效编码" |
| 逗号分隔多编码 | 返回"请逐个识别" |
| AI已识别过的编码 | 路由提示词指导：不重复识别 |

## 输出示例

**商品-主编码-普通**:
```
编码识别: DBTXL01
✓ 商品存在 | 编码类型: 主编码(outer_id)
商品类型: 普通(type=0) | 名称: 短袖T恤
系统ID: item_id=12345
SKU(3个): DBTXL01-01(sku_id=67890), DBTXL01-02(sku_id=67891), DBTXL01-03(sku_id=67892)
```

**商品-主编码-套件**:
```
编码识别: TJ-CCNNTXL01-01
✓ 商品存在 | 编码类型: 主编码(outer_id)
商品类型: SKU套件(type=1) ⚠ 套件没有独立库存
名称: 天竺棉套装 | 系统ID: item_id=23456
```

**商品-SKU编码**:
```
编码识别: DBTXL01-02
✓ 商品存在 | 编码类型: SKU编码(sku_outer_id)
对应主编码: DBTXL01 | 规格: 黑色 XL
系统ID: sku_id=67891 | 商品类型: 普通(type=0)
```

**订单号**:
```
编码识别: 126036803257340376
✓ 订单存在 | 编码类型: 平台订单号(order_id)
平台: 淘宝(18位) | 系统单号: sid=5759422420146938
买家: xxx | 状态: 已发货
```

**不存在**:
```
编码识别: XXXXXX
✗ 未识别到任何匹配
已尝试: 商品主编码、SKU编码
建议: 请确认编码拼写是否正确
```

## 文件清单（4个文件）

### 1. 新建 `backend/services/kuaimai/code_identifier.py`（~200行）

```python
async def identify_code(client: KuaiMaiClient, code: str) -> str:
    """通用编码识别入口（统一接口，其他ERP实现同签名）"""
    # Step 0: 输入校验
    code = code.strip()
    if not code:
        return "请提供有效编码"
    if "," in code:
        return "erp_identify 只支持单个编码，请逐个识别"

    # Step 1: 格式预判 → 分发
    code_type = _guess_code_type(code)

    # Step 2: 按分支识别（失败自动回退商品分支）
    if code_type == "barcode":
        result = await _identify_barcode(client, code)
        if result:
            return result
        # 回退到商品分支
    if code_type.startswith("order_"):
        result = await _identify_order(client, code, code_type)
        if result:
            return result
        # 回退到商品分支（纯数字也可能是商品编码）

    return await _identify_product(client, code)

def _guess_code_type(code: str) -> str:
    """纯规则判断，不调API"""
    ...

async def _identify_product(client, code) -> str:
    """商品编码识别：item.single.get → sku.get
    每个API调用 try-except，异常跳过继续"""
    ...

async def _identify_order(client, code, code_type) -> Optional[str]:
    """订单号识别。返回 None 表示未找到，触发回退"""
    ...

async def _identify_barcode(client, code) -> Optional[str]:
    """条码识别。返回 None 表示未找到，触发回退"""
    ...
```

关键设计：
- 条码/订单分支返回 `Optional[str]`，None 时自动回退到商品分支
- 商品分支是最终兜底，不返回 None
- 每个 API 调用包裹 try-except，异常不中断流程

### 2. 修改 `backend/services/tool_executor.py`（~20行）

- 新增 `_erp_identify_handler(args)` 方法
- 注册到 `_handlers["erp_identify"]`

### 3. 修改 `backend/config/erp_tools.py`（~25行）

**a) 工具定义**（build_erp_tools 中新增）：
```python
{
    "type": "function",
    "function": {
        "name": "erp_identify",
        "description": "识别任意编码/单号的类型和关联信息。输入裸值，返回：编码类型、商品类型、关联参数(ID/编码/名称)。查库存/订单/采购等操作前先用这个确认编码身份，避免参数猜错",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要识别的编码或单号"}
            },
            "required": ["code"]
        }
    }
}
```

**b) 路由提示词**（ERP_ROUTING_PROMPT 新增段）：
```
## 编码识别（前置步骤）
- 首次遇到编码/单号时，先 erp_identify(code=XX) 识别类型和关联参数
- 返回：编码类型 + 商品/订单完整信息 + 下游查询所需的所有ID
- 套件(type=1/2)没有独立库存 → 告知用户需查子单品
- 识别后用返回的精确参数查询，不猜不试
- 同一编码在同一对话中只需识别一次，后续直接用已返回的参数
```

### 4. 新建 `backend/tests/test_code_identifier.py`（~120行）

**_guess_code_type 测试**（纯规则，无API）：
- "6901234567890" → barcode
- "126036803257340376" → order_18
- "1234567890123456789" → order_19
- "P123456789012345678" → order_xhs
- "260305-123456789" → order_pdd
- "5759422420146938" → order_16_or_sid
- "DBTXL01-02" → product
- "8001" → product（短纯数字不是订单）
- "" → 空字符串校验
- "ABC,DEF" → 多编码校验

**_identify_product 测试**（Mock API）：
- 主编码命中（普通）→ 返回 item_id + SKU 列表
- 主编码命中（套件）→ 返回 type=1 + 套件提示
- SKU 编码命中 → 返回 sku_id + 对应主编码
- 商品不存在 → "未识别"提示
- API 异常 → 跳过继续，不中断

**_identify_order 测试**（Mock API）：
- 18位数字找到订单 → 返回 order_id + system_id
- 16位数字先试tid无→试sid找到 → 返回
- 订单不存在 → 返回 None（触发回退）

**回退机制测试**：
- 16位纯数字：订单分支未命中 → 回退商品分支命中 → 正确返回商品信息
- 条码格式但multicode未命中 → 回退商品分支

## 不改动的文件

- `dispatcher.py` — erp_identify 不走 dispatcher，直接用 KuaiMaiClient
- `param_guardrails.py` — 宽泛查询保留作为兜底安全网
- `registry/` — 不新增 entry，erp_identify 独立于 registry 体系
- `formatters/` — erp_identify 自己格式化结果

## 验证

1. `python -m pytest tests/ -q --tb=short`
2. 部署后实测：
   - "TJ-CCNNTXL01-01这个商品还有货？" → 先 erp_identify → 套件 → 告知用户
   - "DBTXL01-02 库存多少？" → 先 erp_identify → SKU → 精准 stock_status(sku_outer_id)
   - "126036803257340376 这个订单什么情况？" → 先 erp_identify → 淘宝订单 → 精准 order_list(order_id)
