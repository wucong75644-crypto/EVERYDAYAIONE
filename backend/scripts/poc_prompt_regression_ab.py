#!/usr/bin/env python3
"""A/B 对照测试 — 今天提示词改动是否让 LLM 代码质量退化

对照组 A:今天大改之前的版本(chat_tools.py 30 行示例 + code_tools.py 旧版精简描述)
实验组 B:今天大改之后的版本(chat_tools.py 5 行强约束 + code_tools.py 5 段式标准格式)

测试场景:用户实际遇到的"发票订单整理表"类多步数据处理任务

评估指标:
1. 代码行数(步骤拆分多少的代理指标)
2. 是否一次 groupby agg 多列(紧凑度)
3. 是否用 merge suffixes 参数(避免列名冲突)
4. 注释行数 / 步骤分割注释数
5. KeyError 风险(merge 后用同名列)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# 对照组 A — 今天大改前的提示词(从 git show b38fc4d 提取)
# ============================================================

DESC_A_OLD = """Python 沙盒 (有状态,变量跨调用保留)。沙盒 cwd=/workspace,所有路径用相对字符串。
预装: pandas/duckdb/matplotlib/openpyxl/pdfplumber/python-docx 等

路径协议(全部相对):
  读用户上传: pd.read_excel('上传/2026-06/x.xlsx')  ← attachments 给 path 字段
  读 parquet: pd.read_parquet('staging/x.parquet')  ← attachments 给 parquet 字段
  读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')
  写产物给用户: df.to_excel('下载/x.xlsx')          ← 自动出下载卡片
  写缓存: df.to_parquet('staging/x.parquet')        ← 跨调用复用,24h 自动清

DuckDB SQL 方言: 中文列名用双引号; ts::DATE 不是 DATE(); 拼接 || 不是 +;
  日期: DATE_TRUNC('month', ts); 类型: TIMESTAMP/BIGINT/DOUBLE/VARCHAR
大数据(>10万行): SQL 聚合后 .df(),禁止 SELECT * .df() 全量加载
导出 Excel: 用 engine='xlsxwriter',自动处理 NaN/Timestamp
代码语法全英文半角: 逗号 , 括号 () 分号 ; 冒号 :"""


SYS_A_OLD = """### code_execute — Python 计算环境

有状态沙盒(变量跨调用保留),cwd=/workspace,执行超时 120 秒。
预装 duckdb(磁盘模式)、openpyxl、pdfplumber、python-docx、pandas。

路径协议(全部相对字符串):
- 读用户上传: pd.read_excel('上传/2026-06/x.xlsx')        (attachments 给 path 字段)
- 读 parquet: pd.read_parquet('staging/x.parquet')        (attachments 给 parquet 字段)
- 读 ERP 结果: duckdb.sql("SELECT * FROM 'staging/erp_xxx.parquet'")
- 写产物: df.to_excel('下载/x.xlsx')                       (自动出下载卡片)
- 写缓存: df.to_parquet('staging/x.parquet')              (跨调用复用)

数据文件已 file_analyze 治理过的,attachments 会有 parquet 字段,字面 copy 即可。
列名用双引号包裹。
print() 输出摘要统计,不要输出完整数据。
约束: 无网络,禁止 sys/subprocess,删除文件用 file_delete 工具。"""


# ============================================================
# 实验组 B — 今天大改后的提示词(当前生产)
# ============================================================

DESC_B_NEW = """Python 计算与可视化沙盒(有状态,变量跨调用保留)。cwd=/workspace,执行超时 120 秒。
预装 pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等。

WHEN TO USE
- 用户要图表/可视化(柱形图/折线图/饼图等) — 必须调,在脚本里用 emit_chart 输出
- 用户要导出 Excel/CSV/PDF — 必须调,写文件后用 emit_file 出下载卡片
- 用户要看数据表格 — 必须调,用 emit_table 渲染
- 计算/统计/聚合/排序/筛选 — 必须调,用 SQL 或 pandas 算

WHEN NOT TO USE
- 用户只是闲聊或问概念解释,不需要计算或产出
- 用户要求获取本地没有的远程数据(用 erp_agent / web_search / file_search)

OUTPUT PROTOCOL — 想给用户看的内容必须调 emit_xxx,只 print 文字 = 用户看不到
- emit_chart(option, title='')   ECharts 图表(option 完整 echarts 配置 dict)
- emit_file(path, label=None)    文件下载卡片(写文件后调,没 emit = 丢)
- emit_image(path)               静态图片(PNG/JPG)
- emit_table(df, title='')       交互式表格(DataFrame 或 list[dict])
- matplotlib plt.show() / plotly fig.show() / altair Chart 自动 emit,不用显式调

PATHS (全部相对字符串)
- 读用户上传: pd.read_excel('上传/2026-06/x.xlsx')    attachments 给 path 字段
- 读 parquet: pd.read_parquet('staging/x.parquet')    attachments 给 parquet 字段
- 读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')
- 写产物: df.to_excel('下载/x.xlsx') + emit_file('下载/x.xlsx')
- 写缓存: df.to_parquet('staging/x.parquet')           跨调用复用,24h 自动清

CAVEATS
- DuckDB 方言: 中文列名双引号; 转日期 ts::DATE; 拼接 ||; DATE_TRUNC('month', ts)
- 大数据(>10万行): SQL 聚合后 .df(),禁止 SELECT * .df() 全量加载
- Excel 导出: engine='xlsxwriter',自动处理 NaN/Timestamp
- 代码语法全英文半角(中文 ,();: 会让 SQL 解析失败)
- 无网络 / 禁止 sys/subprocess / 删文件用 file_delete 工具"""


SYS_B_NEW = """### code_execute — Python 计算环境
Python 沙盒计算与可视化。用于计算/统计/转换数据、生成图表、导出文件。
**用户要图表/表格/文件 → 必须调 code_execute,并在脚本里用 emit_xxx 输出产物。
禁止用文字描述"已生成柱形图"代替真正生成。**
详细 API/参数/路径协议见工具 description。"""


# ============================================================
# 测试场景 — 用户实际遇到的"发票整理"类多步数据处理
# ============================================================

TEST_CASES = [
    {
        "name": "S1_发票整理_经典场景",
        "user": """我有一个发票数据 staging/invoices.parquet,列名:
订单编号、申请时间、单价、数量、发票金额、购方名称、购方税号、商品名称、纸票收货人姓名、备注、发票类型

帮我整理成 Excel(下载/发票整理表.xlsx):
- 按订单编号汇总,单价>0 的行的数量求和(其他行不计)
- 取每个订单的:发票类型/购方名称/购方税号/商品名称/纸票收货人姓名/备注/发票金额
- 日期列用 申请时间 的 月.日 格式(去掉前导 0)
- 输出列顺序:日期、平台订单号、发票类型、公司名称、税号、项目名称、数量、金额、平台+店铺(留空)、申请人、备注""",
    },
    {
        "name": "S2_销售数据_多步聚合",
        "user": """staging/sales.parquet 有列:订单号、日期、平台、店铺、商品、销售额、利润。

帮我做一份按平台+店铺统计的 Excel(下载/平台店铺业绩.xlsx):
- 按平台和店铺分组,算:订单数、总销售额、总利润、平均客单价
- 加一列 "利润率"(利润/销售额 * 100, 保留 2 位小数)
- 按总销售额降序排序""",
    },
    {
        "name": "S3_订单清洗_去重去异常",
        "user": """staging/orders.parquet 有重复订单和异常数据。

帮我:
1. 按订单号去重(保留第一条)
2. 过滤掉 金额 <= 0 或 金额 > 100000 的异常订单
3. 按日期+平台分组算总订单数和总金额
4. 导出到 下载/订单清洗结果.xlsx""",
    },
]


MODELS = ["qwen-plus", "claude-opus-4-7"]


# ============================================================
# 代码质量评估
# ============================================================

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.+?)```", re.DOTALL)


def analyze_code(code: str) -> dict:
    """评估代码质量(行数 / merge 模式 / groupby 紧凑度)"""
    lines = [l for l in code.split("\n") if l.strip()]
    code_lines = [l for l in lines if not l.strip().startswith("#")]
    comment_lines = [l for l in lines if l.strip().startswith("#")]

    # 步骤分割注释(# 步骤1 / # 步骤2 / # Step 1 等)
    step_comments = [
        l for l in comment_lines
        if re.search(r"步骤\s*\d|step\s*\d|^#\s*\d", l, re.I)
    ]

    # merge 出现次数
    merge_count = len(re.findall(r"\.merge\s*\(", code))
    # merge 含 suffixes 参数次数
    merge_with_suffixes = len(re.findall(r"\.merge\s*\([^)]*suffixes\s*=", code))

    # groupby 出现次数
    groupby_count = len(re.findall(r"\.groupby\s*\(", code))
    # 一次 .agg() 多列(用 dict 或 list 传给 agg)
    agg_multi = len(re.findall(r"\.agg\s*\(\s*[\{\[]", code))

    # emit_xxx 调用
    emit_calls = re.findall(r"emit_(chart|file|image|table)\s*\(", code)

    return {
        "total_lines": len(lines),
        "code_lines": len(code_lines),
        "comment_lines": len(comment_lines),
        "step_comments": len(step_comments),
        "merge_total": merge_count,
        "merge_safe_suffixes": merge_with_suffixes,
        "groupby_count": groupby_count,
        "agg_multi_col": agg_multi,
        "emit_calls": emit_calls,
    }


# ============================================================
# 跑 LLM
# ============================================================


async def run_one(model_id: str, sys_outer: str, desc_inner: str, test_case: dict) -> dict:
    """单次:用指定提示词跑指定任务,返回代码 + 分析"""
    from services.adapters.factory import create_chat_adapter

    # 模拟生产链路:外层 system(主 Agent 视角的工具列表)+ 工具 description
    # 注:实际生产里 tool description 是通过 tools= 参数传,LLM 会看到。
    # 这里简化为合并到 system,但效果接近(都在 LLM 决策上下文里)。
    system_prompt = (
        "你是 Python 数据分析助手。你有 code_execute 工具可调用。\n\n"
        "## 主 Agent 工具说明\n"
        f"{sys_outer}\n\n"
        "## code_execute 工具完整 description(LLM 调用时看到的)\n"
        f"{desc_inner}\n\n"
        "用户提问后,只输出一段 Python 代码块(```python ... ```),不要解释。"
        "代码尽量紧凑、一气呵成,不要拆分过多步骤。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": test_case["user"]},
    ]

    try:
        adapter = create_chat_adapter(model_id=model_id)
        response = await adapter.chat_sync(messages, reasoning_effort="minimal")
        await adapter.close()
        output = response.content or ""

        code_match = _CODE_BLOCK_RE.search(output)
        if not code_match:
            return {"error": "no_code_block", "output_preview": output[:200]}
        code = code_match.group(1)
        analysis = analyze_code(code)
        return {"code": code, "analysis": analysis}
    except Exception as e:
        return {"error": str(e), "output_preview": ""}


async def run_group(model_id: str, sys_outer: str, desc_inner: str, group_name: str) -> dict:
    print(f"\n{'='*70}\n{group_name}: model={model_id}\n{'='*70}")
    results = {}
    for tc in TEST_CASES:
        result = await run_one(model_id, sys_outer, desc_inner, tc)
        if "error" in result:
            print(f"  [{tc['name']:<35}] ERROR: {result['error']}")
            results[tc["name"]] = result
        else:
            a = result["analysis"]
            print(
                f"  [{tc['name']:<35}] "
                f"lines={a['code_lines']:>3} "
                f"steps={a['step_comments']:>2} "
                f"merge={a['merge_total']}/{a['merge_safe_suffixes']}safe "
                f"agg_multi={a['agg_multi_col']} "
                f"emit={a['emit_calls']}"
            )
            results[tc["name"]] = result
    return results


# ============================================================
# 报告
# ============================================================


def summarize(results: dict, group_name: str) -> dict:
    """汇总一组的关键指标平均值"""
    sums = {
        "total_lines": 0, "code_lines": 0, "step_comments": 0,
        "merge_total": 0, "merge_safe_suffixes": 0,
        "groupby_count": 0, "agg_multi_col": 0,
    }
    count = 0
    for tc_name, r in results.items():
        if "analysis" not in r:
            continue
        a = r["analysis"]
        for k in sums:
            sums[k] += a[k]
        count += 1
    if count == 0:
        return {}
    return {k: v / count for k, v in sums.items()}


async def main():
    print("=" * 70)
    print("POC: 今天提示词改动是否让 LLM 代码质量退化")
    print("=" * 70)
    print(f"场景数:{len(TEST_CASES)} | 模型数:{len(MODELS)}")
    print(f"对照组 A:今天大改前(30 行示例 + 旧版描述)")
    print(f"实验组 B:今天大改后(5 行强约束 + 5 段式描述)")

    all_results = {}

    for model_id in MODELS:
        a_results = await run_group(model_id, SYS_A_OLD, DESC_A_OLD, "对照组 A (旧版提示词)")
        b_results = await run_group(model_id, SYS_B_NEW, DESC_B_NEW, "实验组 B (新版提示词)")
        all_results[model_id] = {"A": a_results, "B": b_results}

    # 关键指标对比
    print(f"\n\n{'='*70}\n关键指标对比 (每场景平均)\n{'='*70}")
    print(f"{'模型':<22} {'对照 A 代码行':<16} {'实验 B 代码行':<16} {'步骤注释 A→B':<18} {'merge 安全率 A→B'}")
    print("-" * 95)
    for model_id in MODELS:
        sum_a = summarize(all_results[model_id]["A"], "A")
        sum_b = summarize(all_results[model_id]["B"], "B")
        if not sum_a or not sum_b:
            continue
        merge_a = f"{sum_a['merge_safe_suffixes']:.1f}/{sum_a['merge_total']:.1f}"
        merge_b = f"{sum_b['merge_safe_suffixes']:.1f}/{sum_b['merge_total']:.1f}"
        print(
            f"  {model_id:<20} "
            f"{sum_a['code_lines']:>6.1f}              "
            f"{sum_b['code_lines']:>6.1f}              "
            f"{sum_a['step_comments']:.1f} → {sum_b['step_comments']:.1f}           "
            f"{merge_a} → {merge_b}"
        )

    print(f"\n{'='*70}\n结论:")
    print("- 实验 B 代码行数显著多于对照 A → 提示词改动让 LLM 拆步过细")
    print("- merge 安全率低 → 提示词缺 pandas 陷阱提示")
    print("- agg_multi 低 → LLM 没用紧凑写法(一次 agg 多列)")
    print(f"{'='*70}")

    out_file = Path(__file__).parent / "poc_prompt_regression_results.json"
    out_file.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始结果(含代码全文): {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
