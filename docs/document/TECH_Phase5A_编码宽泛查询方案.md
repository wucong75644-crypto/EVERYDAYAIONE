# Phase 5A: 编码宽泛查询 + 双参数重试合并

## 1. 问题

用户通过自然语言查询ERP编码时，存在两种不精准：

1. **编码粒度不对**：传SKU编码 `DBTXL01-02`，但API的 `outer_id` 只接受主编码 `DBTXL01`
2. **编码类型混用**：主编码和SKU编码放到错误的参数里，或批量查询时混着传

当前实现的问题：
- 「精确查询→零结果→宽泛查询」浪费一次API调用
- 宽泛查询只查1页数据不全

## 2. 方案：原始+宽泛打包 + 双参数重试

核心思路：**原始编码和宽泛编码打包在一起发送，同时用两个参数（outer_id和sku_outer_id）分别查询**。

### 2.1 单条编码（outer_id / sku_outer_id）

```
输入: DBTXL01-02
提取基础编码: DBTXL
打包: "DBTXL01-02,DBTXL"（原始+宽泛一起发）

第1步: mainOuterId="DBTXL01-02,DBTXL" → fetch_all → 本地匹配DBTXL01-02
  → 找到 → 返回
  → 没找到 ↓
第2步: skuOuterId="DBTXL01-02,DBTXL" → fetch_all → 本地匹配DBTXL01-02
```

打包的好处：一次查询同时覆盖"精确命中"和"宽泛命中"两种情况。

### 2.2 批量编码（outer_ids / sku_outer_ids）

```
输入: outer_ids="DBTXL01-02,ABC123,XYZ456-01"
提取基础编码: DBTXL, ABC, XYZ
打包去重: "DBTXL01-02,ABC123,XYZ456-01,DBTXL,ABC,XYZ"

第1步: outerIds=打包编码 → 结果A
第2步: skuOuterIds=打包编码 → 结果B
合并A+B → 去重 → 用原始编码本地匹配 → 重新计算total
```

差异：
- **单条**：依次试（找到就停），因为同一个编码不可能同时是主编码和SKU编码
- **批量**：两个参数都查+合并，因为混着主编码和SKU编码，一个参数查不全

### 2.3 API编码数量上限（实测）

| API | 参数 | 上限 | 测试结果 |
|-----|------|------|---------|
| stock_status | mainOuterId / skuOuterId | **5000+** | 5000个(61KB)正常，限制在请求体大小而非数量 |
| multi_product | outerIds | **20** | 21个报错 20112 |
| item_supplier_list | outerIds / skuOuterIds | **20** | 21个报错 20112/20309 |
| warehouse_stock | outerId / skuOuterId | **仅1个** | 2个报错 20302/20303 |
| stock_update | outerIds / skuOuterIds | **20** | error_code 20112 |
| virtual_stock_update | outerIds / skuOuterIds | **20** | error_code 20112/20309 |

**打包后的数量控制**：
- stock_status：无压力，原始+宽泛随便打包
- outerIds/skuOuterIds系列：原始N个+宽泛最多N个=2N，若 >20 则放弃宽泛，只用原始编码
- warehouse_stock：单值，不存在打包问题

## 3. 安全门（3道）

| 安全门 | 规则 | 排除的API |
|-------|------|----------|
| ① is_write | 写操作跳过 | stock_update, virtual_stock_update, supplier_add/delete, product_add_update_simple 等 |
| ② single_code_only | ApiEntry标志，仅支持单值的API跳过打包 | warehouse_stock（不打包，但仍做双参数依次试） |
| ③ _find_code_param | 识别 outer_id / sku_outer_id / outer_ids / sku_outer_ids | 订单(order_id), 采购(code), 仓库单据(code), 售后 等 |

### 触发的API

**单条模式**（原始+宽泛打包 → 双参数依次试）：

| API | outer_id映射 | sku_outer_id映射 | 编码上限 | 备注 |
|-----|-------------|-----------------|---------|------|
| stock_status | mainOuterId | skuOuterId | 5000+ | 打包+翻页 |
| warehouse_stock | outerId | skuOuterId | 仅1个 | `single_code_only=True`，不打包，原始编码依次试两个参数 |
| stock_in_out | outerId | 无 | 待验证 | 仅1个key，依次试 |
| sku_list | outerId | 无 | 待验证 | 仅1个key，依次试 |

**批量模式**（原始+宽泛打包 → 查询+合并）：

| API | outer_ids映射 | sku_outer_ids映射 | 编码上限 | 备注 |
|-----|-------------|------------------|---------|------|
| item_supplier_list | outerIds | skuOuterIds | 20 | 双参数都查+合并 |
| distributor_item_list | outerIds | skuOuterIds | 保守按20 | 双参数都查+合并 |
| supplier_view_item_list | outerIds | skuOuterIds | 保守按20 | 双参数都查+合并 |
| multi_product | outerIds | 无 | 20 | 单参数宽泛打包（无双参数可合并） |
| outer_id_list | outerIds | 无 | 20 | 单参数宽泛打包（无双参数可合并） |

## 4. 文件清单

| 文件 | 操作 |
|------|------|
| `backend/services/kuaimai/registry/base.py` | ApiEntry 新增 `single_code_only: bool = False` |
| `backend/services/kuaimai/registry/product.py` | warehouse_stock 加 `single_code_only=True` + param_docs标注编码数量上限 |
| `backend/services/kuaimai/registry/distribution.py` | param_docs标注编码数量上限（已完成） |
| `backend/services/kuaimai/param_guardrails.py` | 删2旧函数 + 改_find_code_param + 新增4函数 |
| `backend/services/kuaimai/dispatcher.py` | 步骤4重构 |
| `backend/tests/test_param_guardrails.py` | 删旧测试 + 新增4测试类 |

## 5. 详细改动

### 5.1 param_guardrails.py

**修改 `_find_code_param()`** — 同时识别单数+复数：
```python
def _find_code_param(
    user_params: Dict[str, Any],
) -> Optional[Tuple[str, str, bool]]:
    """找到查询中使用的编码参数

    Returns:
        (参数名, 编码值, is_batch) 或 None
    """
    # 单数优先
    for key in ("sku_outer_id", "outer_id"):
        val = user_params.get(key)
        if val:
            return key, str(val), False
    # 复数
    for key in ("sku_outer_ids", "outer_ids"):
        val = user_params.get(key)
        if val:
            return key, str(val), True
    return None
```

**删除**（~80行）：`broadened_code_query()` + `_try_broadened_query()`

**新增① `apply_code_broadening()`**（~50行）— API调用前预处理：
```python
def apply_code_broadening(
    entry: ApiEntry, user_params: Dict[str, Any], api_params: Dict[str, Any],
) -> Optional[Tuple[str, str, List[str], bool]]:
    """编码宽泛化预处理

    Returns:
        (原始编码, 打包编码, [可用API参数key], is_batch) 或 None
    """
    if entry.is_write:
        return None
    code_info = _find_code_param(user_params)
    if not code_info:
        return None
    param_name, original_codes, is_batch = code_info

    if is_batch:
        # 批量模式：收集可用参数key（≥1个即可）
        api_keys = []
        for p in ("outer_ids", "sku_outer_ids"):
            k = entry.param_map.get(p)
            if k:
                api_keys.append(k)
        if not api_keys:
            return None

        # 打包：原始编码 + 各编码的基础编码（有序去重）
        code_list = [c.strip() for c in original_codes.split(",") if c.strip()]
        seen = set()
        broadened = []
        for code in code_list:
            if code not in seen:
                seen.add(code)
                broadened.append(code)
            base = extract_base_code(code)
            if base and base not in seen:
                seen.add(base)
                broadened.append(base)
        packed = ",".join(broadened)

        # 超过20个则放弃宽泛，只用原始编码
        if len(broadened) > 20:
            packed = original_codes

        for k in ("outerIds", "skuOuterIds"):
            api_params.pop(k, None)
        return original_codes, packed, api_keys, True
    else:
        # 单条模式
        api_keys = []
        for p in ("outer_id", "sku_outer_id"):
            k = entry.param_map.get(p)
            if k:
                api_keys.append(k)
        if not api_keys:
            return None

        for k in ("mainOuterId", "skuOuterId", "outerId"):
            api_params.pop(k, None)

        # single_code_only：不打包，只用原始编码依次试两个参数
        if entry.single_code_only:
            return original_codes, original_codes, api_keys, False

        # 正常打包：原始编码 + 基础编码
        base_code = extract_base_code(original_codes)
        if base_code:
            packed = f"{original_codes},{base_code}"
            api_params["pageSize"] = 100
        else:
            # 纯数字等无法宽泛，但仍做双参数依次试
            packed = original_codes

        return original_codes, packed, api_keys, False
```

**新增② `_fetch_all_with_limit()`**（~30行）— 独立翻页函数（复用 dispatcher._fetch_all_pages 逻辑）：
```python
async def _fetch_all_with_limit(
    client: Any,
    method: str,
    params: Dict[str, Any],
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
    response_key: str = "list",
    max_pages: int = 10,
) -> Dict[str, Any]:
    """独立翻页拉取，带页数上限保护"""
    page_size = int(params.get("pageSize", 100))
    all_items: list = []
    last_data: Dict[str, Any] = {}

    for page in range(1, max_pages + 1):
        params["pageNo"] = page
        data = await client.request_with_retry(
            method, params,
            base_url=base_url,
            extra_system_params=system_params,
        )
        last_data = data
        items = data.get(response_key) or []
        all_items.extend(items)
        if len(items) < page_size:
            break

    last_data[response_key] = all_items
    return last_data
```

**新增③ `try_broadened_queries()`**（~60行）— 单条模式：打包+依次试：
```python
async def try_broadened_queries(
    entry: ApiEntry,
    api_params: Dict[str, Any],
    original_code: str,
    packed_code: str,
    api_keys: List[str],
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str]:
    """单条宽泛查询：原始+宽泛打包发送，依次用每个参数查，找到就停"""
    response_key = entry.response_key  # 不要 or "list"，None 表示 detail API

    for i, api_key in enumerate(api_keys):
        query_params = dict(api_params)
        query_params[api_key] = packed_code
        param_label = "outer_id" if i == 0 else "sku_outer_id"
        try:
            if response_key:
                # List API：翻页拉取 + 本地匹配
                data = await _fetch_all_with_limit(
                    client, entry.method, query_params,
                    base_url, system_params,
                    response_key=response_key, max_pages=10,
                )
                items = data.get(response_key) or []
                if not items:
                    continue
                matched = _match_items(items, original_code)
                if not matched:
                    continue
                result = dict(data)
                result[response_key] = matched
                result["total"] = len(matched)
                note = (
                    f"⚙ 编码智能匹配: 「{original_code}」→ "
                    f"打包「{packed_code}」({param_label})查到{len(items)}条，"
                    f"匹配到{len(matched)}条"
                )
                return result, note
            else:
                # Detail API（如warehouse_stock）：单次查询，成功即命中
                data = await client.request_with_retry(
                    entry.method, query_params,
                    base_url=base_url,
                    extra_system_params=system_params,
                )
                note = f"⚙ 双参数依次试: {param_label}={packed_code} → 命中"
                return data, note
        except Exception as e:
            logger.warning(f"BroadenedQuery error | key={api_key} | {e}")
            continue

    note = f"⚙ 编码智能匹配: 「{original_code}」所有参数均无匹配"
    empty = {response_key: [], "total": 0} if response_key else {}
    return empty, note
```

**新增④ `try_batch_dual_query()`**（~60行）— 批量模式：打包+查询+合并：
```python
async def try_batch_dual_query(
    entry: ApiEntry,
    api_params: Dict[str, Any],
    original_codes: str,
    packed_codes: str,
    api_keys: List[str],
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str]:
    """批量双参数查询：打包编码分别用两个参数查，合并去重"""
    response_key = entry.response_key or "list"
    all_items: List[Dict] = []
    query_labels = []

    for i, api_key in enumerate(api_keys):
        query_params = dict(api_params)
        query_params[api_key] = packed_codes
        try:
            data = await client.request_with_retry(
                entry.method, query_params,
                base_url=base_url,
                extra_system_params=system_params,
            )
        except Exception as e:
            logger.warning(f"BatchDualQuery error | key={api_key} | {e}")
            continue

        items = data.get(response_key) or []
        param_label = "outer_ids" if i == 0 else "sku_outer_ids"
        query_labels.append(f"{param_label}={len(items)}条")
        all_items.extend(items)

    # 合并去重
    deduped = _deduplicate_items(all_items)

    # 用原始编码做本地匹配
    original_list = [c.strip() for c in original_codes.split(",") if c.strip()]
    matched = _match_items_batch(deduped, original_list)

    result = {response_key: matched, "total": len(matched)}
    note = (
        f"⚙ 批量双参数查询: "
        f"{' + '.join(query_labels)}，"
        f"合并去重后{len(deduped)}条，匹配原始编码后{len(matched)}条"
    )
    return result, note


def _deduplicate_items(items: List[Dict]) -> List[Dict]:
    """按编码+ID字段组合去重"""
    seen = set()
    result = []
    for item in items:
        key_parts = []
        for field in ("outerId", "skuOuterId", "mainOuterId",
                       "sysItemId", "sysSkuId"):
            val = item.get(field, "")
            if val:
                key_parts.append(f"{field}={val}")
        key = "|".join(key_parts) if key_parts else str(id(item))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _match_items_batch(items: List[Dict], original_codes: List[str]) -> List[Dict]:
    """批量编码本地匹配：保留与任一原始编码匹配的条目"""
    needles = {code.upper() for code in original_codes}
    matched = []
    for item in items:
        for field in _CODE_MATCH_FIELDS:
            val = str(item.get(field, "")).upper()
            if val and val in needles:
                matched.append(item)
                break
    # 如果精确匹配为空，返回全部（宽泛编码可能命中了但原始没有精确匹配）
    return matched if matched else items
```

**保留不变**：extract_base_code / _match_items / _is_empty_result / diagnose_empty_result / preprocess_params

### 5.2 dispatcher.py

导入改为：`apply_code_broadening, try_broadened_queries, try_batch_dual_query`

（不需要导入 `_match_items_batch`，`try_batch_dual_query` 内部已包含本地匹配）

步骤4重构：
```python
# 4.1 编码宽泛化预处理
broadening = apply_code_broadening(entry, params, api_params)

broadened_note = ""
if broadening:
    original_codes, packed_codes, api_keys, is_batch = broadening
    if is_batch:
        # 批量模式：try_batch_dual_query 统一处理1个或2个api_keys
        # 1个key→查1次+本地匹配；2个key→查2次+合并去重+本地匹配
        data, broadened_note = await try_batch_dual_query(
            entry, api_params, original_codes, packed_codes, api_keys,
            self._client, base_url, system_params,
        )
    else:
        # 单条模式：打包 + 依次试
        data, broadened_note = await try_broadened_queries(
            entry, api_params, original_codes, packed_codes, api_keys,
            self._client, base_url, system_params,
        )
else:
    # 正常模式
    try:
        if entry.fetch_all:
            data = await self._fetch_all_pages(entry, api_params, base_url, system_params)
        else:
            data = await self._client.request_with_retry(...)
    except Exception as e:
        ...
```

### 5.3 registry param_docs 标注编码数量上限

**product.py**：
- stock_status.outer_id: 加"支持逗号分隔多个编码"
- stock_status.sku_outer_id: 加"支持逗号分隔多个编码"
- warehouse_stock.outer_id: 加"仅支持单个编码"
- warehouse_stock.sku_outer_id: 加"仅支持单个编码"
- multi_product.outer_ids: 加"最多20个"
- item_supplier_list.outer_ids: 加"最多20个"
- item_supplier_list.sku_outer_ids: 加"最多20个"
- stock_update.outer_ids: 加"最多20个"
- virtual_stock_update.outer_ids/sku_outer_ids: 加"最多20个"

**distribution.py**：
- distributor_item_list.outer_ids/sku_outer_ids: 加"最多20个"
- supplier_view_item_list.outer_ids/sku_outer_ids: 加"最多20个"

### 5.4 tests/test_param_guardrails.py

删除 `TestBroadenedCodeQuery`（8个旧测试）

**新增 `TestFindCodeParam`**（6个）：
- 单数 outer_id → is_batch=False
- 单数 sku_outer_id → is_batch=False
- 复数 outer_ids → is_batch=True
- 复数 sku_outer_ids → is_batch=True
- 单数优先于复数（同时存在时）
- 无编码参数 → None

**新增 `TestApplyCodeBroadening`**（10个）：
- 单条正常：返回打包编码 "DBTXL01-02,DBTXL"
- 单条纯数字编码：packed=原始编码，api_keys仍返回（双参数依次试）
- 单条 single_code_only：packed=原始编码（不打包）
- 批量双参数：返回原始+宽泛打包，api_keys=2个
- 批量单参数：返回原始+宽泛打包，api_keys=1个（不跳过）
- 批量超20个放弃宽泛，packed=original
- 写操作跳过
- 无编码参数跳过
- api_params原有编码被清除
- 批量无任何编码参数时跳过

**新增 `TestTryBroadenedQueries`**（7个）：
- List API：第一个参数就匹配到
- List API：第一个参数无匹配，第二个参数匹配到
- List API：两个参数都无匹配
- Detail API（response_key=None）：第一个参数成功即返回
- Detail API：第一个异常，第二个成功
- API异常时跳过继续
- 空数据

**新增 `TestTryBatchDualQuery`**（6个）：
- 两个参数都有数据，合并去重+本地匹配
- 第一个有数据第二个无数据
- 重复数据被去重
- API异常时跳过继续
- 去重后total正确
- 本地匹配无精确命中时返回全部

## 6. 验证
```bash
cd /Users/wucong/EVERYDAYAIONE/backend && source venv/bin/activate
python -m pytest tests/test_param_guardrails.py tests/test_param_mapper.py -v --tb=short
python -m pytest tests/ -q --tb=short
```

## 7. 完整流程举例

### 场景1：单条 — 用户说"查DBTXL01-02的库存"

```
AI: stock_status, outer_id=DBTXL01-02
  → apply_code_broadening:
    is_batch=False, packed="DBTXL01-02,DBTXL", api_keys=[mainOuterId, skuOuterId]
  → try_broadened_queries:
    第1步: mainOuterId="DBTXL01-02,DBTXL" → fetch_all → 匹配DBTXL01-02 → ✅ 找到
```

### 场景2：单条 — AI放错参数

```
AI: stock_status, sku_outer_id=DBTXL01-02
  → apply_code_broadening:
    _find_code_param识别sku_outer_id → is_batch=False
    packed="DBTXL01-02,DBTXL", api_keys=[mainOuterId, skuOuterId]
  → try_broadened_queries:
    第1步: mainOuterId="DBTXL01-02,DBTXL" → fetch_all → 匹配 → ✅
    （不管AI放哪个参数，都从outer_id开始试）
```

### 场景3：单条 — DBTXL01-02本身就是主编码

```
  → mainOuterId="DBTXL01-02,DBTXL"
    API精确匹配到DBTXL01-02 → 本地匹配命中 → ✅
    （打包的好处：原始编码精确命中 + 宽泛编码兜底，一次搞定）
```

### 场景4：批量 — 主编码和SKU编码混着传

```
AI: item_supplier_list, outer_ids=DBTXL01,DBTXL01-02,ABC123
  → apply_code_broadening:
    is_batch=True
    原始: [DBTXL01, DBTXL01-02, ABC123]
    基础: [DBTXL, ABC]
    打包去重: "DBTXL01,DBTXL01-02,ABC123,DBTXL,ABC" (5个 ≤20)
    api_keys=[outerIds, skuOuterIds]
  → try_batch_dual_query:
    查询1: outerIds="DBTXL01,...,ABC" → 返回 [DBTXL01, ABC123]
    查询2: skuOuterIds="DBTXL01,...,ABC" → 返回 [DBTXL01-02]
    合并去重 → 3条
    本地匹配原始编码 → [DBTXL01, DBTXL01-02, ABC123] → ✅ 全找到
```

### 场景5：批量 — 编码数超20

```
AI: item_supplier_list, outer_ids=（12个原始编码）
  → 宽泛后 12+12=24 > 20
  → 放弃宽泛，packed=原始编码
  → 仍然做双参数查询+合并（保底不丢功能）
```

### 场景6：warehouse_stock — single_code_only

```
AI: warehouse_stock, outer_id=DBTXL01-02
  → apply_code_broadening:
    entry.single_code_only=True → 不打包
    packed=原始编码"DBTXL01-02", api_keys=[outerId, skuOuterId]
  → try_broadened_queries:
    第1步: outerId="DBTXL01-02" → 查询 → 有结果 → ✅
    第1步无结果 → 第2步: skuOuterId="DBTXL01-02" → 查询 → ✅
    （不打包宽泛，但仍做双参数依次试，解决"参数放错"的问题）
```

### 场景7：纯数字编码 — 无宽泛但仍双参数

```
AI: stock_status, outer_id=12345
  → apply_code_broadening:
    extract_base_code("12345") = None → 无法宽泛
    packed=原始编码"12345", api_keys=[mainOuterId, skuOuterId]
  → try_broadened_queries:
    第1步: mainOuterId="12345" → 查询 → 有结果 → ✅
    第1步无结果 → 第2步: skuOuterId="12345" → 查询 → ✅
    （不宽泛，但双参数依次试兜底）
```

## 8. 边界情况

| 场景 | 行为 |
|------|------|
| 单条 + 字母数字混合 | 原始+宽泛打包 → 双参数依次试 |
| 单条 + 纯数字/纯字母 | 无宽泛打包，packed=原始编码，仍做双参数依次试 |
| 单条 + single_code_only | 不打包，原始编码依次试两个参数 |
| 批量 + API有两个编码参数 | 打包 → 双参数都查 → 合并去重 → 本地匹配 |
| 批量 + API只有一个参数 | 打包宽泛编码到唯一参数发出去（无双参数可合并） |
| 批量 + 打包后>20个 | 放弃宽泛，只用原始编码（仍做双参数查询+合并） |
| 写操作 | 不干预 |
| 单条超10页 | max_pages=10截断（1000条） |
| 查询异常 | 跳过当前参数，尝试下一个 |
| 本地匹配0条 | 返回宽泛查询全部结果（兜底） |
