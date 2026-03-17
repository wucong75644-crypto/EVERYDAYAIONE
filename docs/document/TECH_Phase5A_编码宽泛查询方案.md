# Phase 5A: 编码直接宽泛查询 + fetch_all翻页

## 问题
当前「精确查询→零结果→宽泛查询」浪费一次API调用，且宽泛查询只查1页数据不全。

## 方案
编码参数直接用基础编码宽泛查 + fetch_all翻页拉全量 + 本地匹配。

## 文件清单

| 文件 | 操作 |
|------|------|
| `backend/services/kuaimai/param_guardrails.py` | 删2旧函数 + 新增2函数 |
| `backend/services/kuaimai/dispatcher.py` | 步骤4重构 + fetch_all加max_pages |
| `backend/tests/test_param_guardrails.py` | 删旧测试 + 新增2测试类 |

## 详细改动

### param_guardrails.py

**删除**（~80行）：`broadened_code_query()` + `_try_broadened_query()`

**新增① `apply_code_broadening()`**（~35行）— API调用**前**介入：
```python
def apply_code_broadening(entry, user_params, api_params) -> Optional[Tuple[str, str]]:
    # 安全门①：写操作跳过
    if entry.is_write:
        return None
    # 安全门②：详情接口跳过（response_key=None）
    if entry.response_key is None:
        return None
    # 安全门③：只认 outer_id/sku_outer_id 单数形式
    code_info = _find_code_param(user_params)
    if not code_info:
        return None
    param_name, original_code = code_info
    base_code = extract_base_code(original_code)
    if not base_code:
        return None

    outer_key = entry.param_map.get("outer_id")
    sku_key = entry.param_map.get("sku_outer_id")
    target_key = outer_key or sku_key
    if not target_key:
        return None

    for k in ("mainOuterId", "skuOuterId", "outerId"):
        api_params.pop(k, None)
    api_params[target_key] = base_code
    api_params["pageSize"] = 100
    return original_code, base_code
```

仅触发4个查询（三重安全门）：

| 安全门 | 排除 |
|-------|------|
| ① is_write | supplier_add/delete, product_add_update_simple, update_history_price, fast_stock_update |
| ② response_key is None | product_detail, sku_info |
| ③ _find_code_param | 订单(order_id), 采购(code), 仓库单据(code), 批量(outer_ids复数), 售后 |

最终触发：stock_status / warehouse_stock / stock_in_out / sku_list

**新增② `match_broadened_results()`**（~25行）— API调用**后**匹配：
```python
def match_broadened_results(entry, data, original_code, base_code) -> Tuple[dict, str]:
    response_key = entry.response_key or "list"
    items = data.get(response_key) or []
    if not items:
        return data, f"⚙ 编码智能匹配: 用基础编码「{base_code}」查询无数据"

    matched = _match_items(items, original_code)
    result_data = dict(data)
    if matched:
        result_data[response_key] = matched
        result_data["total"] = len(matched)
        note = f"⚙ 编码智能匹配: 「{base_code}」查到{len(items)}条，匹配「{original_code}」得{len(matched)}条"
    else:
        note = f"⚙ 编码智能匹配: 「{base_code}」查到{len(items)}条，未匹配「{original_code}」，返回全部"
    return result_data, note
```

**保留不变**：extract_base_code / _find_code_param / _match_items / _is_empty_result / diagnose_empty_result / preprocess_params

### dispatcher.py

导入改为：`apply_code_broadening, match_broadened_results`（替代 `broadened_code_query`）

步骤4重构：
```python
# 4.1 编码宽泛化（API调用前）
broadening = apply_code_broadening(entry, params, api_params)

try:
    if entry.fetch_all or broadening:
        data = await self._fetch_all_pages(
            entry, api_params, base_url, system_params,
            max_pages=10 if broadening else 0,
        )
    else:
        data = await self._client.request_with_retry(...)
except Exception as e:
    ...

# 4.5 本地匹配（替代旧broadened_code_query）
broadened_note = ""
if broadening:
    original_code, base_code = broadening
    data, broadened_note = match_broadened_results(entry, data, original_code, base_code)
```

`_fetch_all_pages` 加 `max_pages` 参数（+5行）：
```python
async def _fetch_all_pages(self, ..., *, max_pages: int = 0):
    ...
    if max_pages and page >= max_pages:
        logger.warning(f"fetch_all hit max_pages={max_pages}")
        break
```

### tests/test_param_guardrails.py

删除 `TestBroadenedCodeQuery`（8个旧测试）

新增 `TestApplyCodeBroadening`（6个）：
- 正常宽泛化（sku_outer_id→base code）
- 写操作跳过
- 详情接口(response_key=None)跳过
- 无编码参数跳过
- 纯数字编码跳过
- 只有sku_outer_id映射时的兜底

新增 `TestMatchBroadenedResults`（4个）：
- 精确匹配命中
- 无匹配返回全部
- 空数据
- response_key缺省用"list"

## 验证
```bash
cd /Users/wucong/EVERYDAYAIONE/backend && source venv/bin/activate
python -m pytest tests/test_param_guardrails.py tests/test_param_mapper.py -v --tb=short
python -m pytest tests/ -q --tb=short
```

## 边界情况

| 场景 | 编码 | 基础编码 | 行为 |
|------|------|---------|------|
| SKU查出入库 | SEVENTEENLSG01-01 | SEVENTEENLSG | outerId=SEVENTEENLSG + fetch_all + 匹配 |
| SKU查库存 | DBTXL01-02 | DBTXL | mainOuterId=DBTXL + fetch_all + 匹配 |
| 纯数字 | 12345 | None | 正常精确查询 |
| 纯字母 | DBTXL | None | 正常精确查询 |
| 写操作 | 任意 | - | 不干预 |
| 超10页 | - | - | max_pages=10截断（100×10=1000条） |
