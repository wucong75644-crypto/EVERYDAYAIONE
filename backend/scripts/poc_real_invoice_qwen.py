#!/usr/bin/env python3
"""真实场景测试 - 千问跑发票整理任务

复现今天用户报的 KeyError: '数量' bug。
- 真实文件 schema(从生产 meta.json 提取的 27 列)
- 真实 attachments XML(模拟 file_analyze 已治理的状态)
- 真实用户 prompt
- qwen-plus 模型
- 跑 5 次,统计 LLM 代码模式
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# 真实 27 列(从生产 meta.json 提取)
REAL_COLS = [
    "订单编号", "发票类型", "购方名称", "购方税号", "购方地址", "购方电话",
    "购方开户行", "购方银行账号", "备注", "商品名称", "规格型号", "单位",
    "数量", "单价", "发票金额", "纸票收货人姓名", "纸票收货人地址",
    "申请时间", "发票状态", "下单时间", "订单完成时间", "开票倒计时开始时间",
    "抬头类型", "申请来源", "特殊订单", " ", "国补区域主体",
]

PARQUET_PATH = "staging/_cache_v3.0_13e4522ac598_sheet0_104960729691_fd1952.parquet"


# 真实生产 attachments XML(file_analyze 治理后的格式)
ATTACHMENTS_XML = f"""<attachments>
  <attachment>
    <name>104960729691_fd1952.xlsx</name>
    <status>analyzed</status>
    <parquet>{PARQUET_PATH}</parquet>
    <summary>1171 行 × 27 列,发票订单数据(含购方信息、商品、数量、金额、申请时间)</summary>
    <columns>{', '.join(REAL_COLS)}</columns>
  </attachment>
</attachments>"""


# 真实用户 prompt(用户原话改写,贴近真实表达)
USER_PROMPT = """帮我整理这个发票数据成 Excel 给我下载,要求:

列顺序: 日期、平台订单号、发票类型、公司名称、税号、项目名称、数量、金额、平台+店铺、申请人、备注

规则:
- 日期: 申请时间 的 月.日 格式(去掉前导 0,比如 6.6 不是 06.06)
- 平台订单号: 订单编号
- 公司名称: 购方名称
- 税号: 购方税号
- 项目名称: 商品名称
- 数量: 同一订单按 单价>0 的行汇总(其他行不计)
- 金额: 发票金额
- 平台+店铺: 留空
- 申请人: 纸票收货人姓名

存到 下载/发票整理表.xlsx"""


# 旧版提示词(今天大改前,git show b38fc4d 提取)
OLD_SYS = """### code_execute — Python 计算环境

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


OLD_DESC = """Python 沙盒 (有状态,变量跨调用保留)。沙盒 cwd=/workspace,所有路径用相对字符串。
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


# 当前生产提示词(commit 05bcd2e 部署的版本)
CURRENT_SYS = """### code_execute — Python 计算环境
Python 沙盒计算与可视化。用于计算/统计/转换数据、生成图表、导出文件。
**用户要图表/表格/文件 → 必须调 code_execute,并在脚本里用 emit_xxx 输出产物。
禁止用文字描述"已生成柱形图"代替真正生成。**
详细 API/参数/路径协议见工具 description。"""


CURRENT_DESC = """Python 计算与可视化沙盒(有状态,变量跨调用保留)。cwd=/workspace,执行超时 120 秒。
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


_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.+?)```", re.DOTALL)


def check_merge_bug(code: str) -> dict:
    """检查代码里是否有 merge 同名列冲突的 bug"""
    has_merge = re.search(r"\.merge\s*\(", code) is not None
    has_suffixes = re.search(r"\.merge\s*\([^)]*suffixes\s*=", code) is not None
    # 检查 merge 前是否 drop 了重复列
    has_drop_before_merge = re.search(r"\.drop\s*\(\s*columns\s*=\s*\[.*?['\"]数量", code, re.DOTALL)

    # 找疑似 KeyError 风险:merge 后用旧名取列(如 df_result['数量'])
    # 如果有 merge 且没有 suffixes 且没有 drop,且 merge 后用了 '数量' → 风险
    merge_pos = code.find(".merge(")
    keyerror_risk = False
    if has_merge and not has_suffixes and not has_drop_before_merge:
        # merge 后的代码段用了 '数量'
        after_merge = code[merge_pos:] if merge_pos >= 0 else ""
        # 简化检测:merge 之后还在用单纯的 '数量'(不是 '数量_x'/'数量_y')
        if re.search(r"['\"]数量['\"](?![_a-zA-Z])", after_merge):
            keyerror_risk = True

    return {
        "has_merge": has_merge,
        "uses_suffixes": has_suffixes,
        "drops_before_merge": bool(has_drop_before_merge),
        "keyerror_risk": keyerror_risk,
    }


def check_emit_file(code: str) -> bool:
    return bool(re.search(r"emit_file\s*\(", code))


def count_steps(code: str) -> int:
    """统计步骤注释数(# 步骤 X 或 # Step X)"""
    return len(re.findall(r"#\s*(?:步骤|step|Step)\s*\d", code))


async def run_one(model_id: str, run_idx: int, version: str = "NEW") -> dict:
    from services.adapters.factory import create_chat_adapter

    sys_outer, desc_inner = (
        (OLD_SYS, OLD_DESC) if version == "OLD" else (CURRENT_SYS, CURRENT_DESC)
    )

    system = (
        "你是 Python 数据分析助手,有 code_execute 工具可调用。\n\n"
        f"## 主 Agent 工具说明\n{sys_outer}\n\n"
        f"## code_execute 详细 description\n{desc_inner}\n\n"
        "用户提问后,直接输出一段 Python 代码块(```python ... ```),不要解释。"
    )

    # 真实生产里 attachments 是在 messages 里作为 system role 注入的
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": ATTACHMENTS_XML},
        {"role": "user", "content": USER_PROMPT},
    ]

    try:
        adapter = create_chat_adapter(model_id=model_id)
        response = await adapter.chat_sync(messages, reasoning_effort="minimal")
        await adapter.close()
        output = response.content or ""

        code_match = _CODE_BLOCK_RE.search(output)
        if not code_match:
            return {"run": run_idx, "error": "no_code_block"}

        code = code_match.group(1)
        merge_info = check_merge_bug(code)
        return {
            "run": run_idx,
            "code_lines": len([l for l in code.split("\n") if l.strip()]),
            "step_comments": count_steps(code),
            "has_emit_file": check_emit_file(code),
            **merge_info,
            "code": code,
        }
    except Exception as e:
        return {"run": run_idx, "error": str(e)}


async def run_group(version: str, n: int = 5) -> list[dict]:
    label = f"{'旧版 (大改前)' if version == 'OLD' else '新版 (当前生产)'}"
    print(f"\n{'='*70}")
    print(f"组 {version}: {label}")
    print(f"{'='*70}")
    results = []
    for i in range(n):
        r = await run_one("qwen-plus", i + 1, version=version)
        results.append(r)
        if "error" in r:
            print(f"  Run {i+1}: ERROR: {r['error']}")
        else:
            risk_flag = "❌ KeyError 风险" if r["keyerror_risk"] else "✅"
            emit_flag = "✅" if r["has_emit_file"] else "❌ 无 emit"
            print(
                f"  Run {i+1}: lines={r['code_lines']:>3} | merge={r['has_merge']} | "
                f"safe={r['uses_suffixes'] or r['drops_before_merge']} | {risk_flag} | {emit_flag}"
            )
    return results


def summarize(results: list[dict], label: str) -> dict:
    ok = [r for r in results if "error" not in r]
    if not ok:
        return {"label": label, "valid": 0}
    return {
        "label": label,
        "valid": len(ok),
        "avg_lines": sum(r["code_lines"] for r in ok) / len(ok),
        "merge_count": sum(1 for r in ok if r["has_merge"]),
        "safe_merge": sum(1 for r in ok if r["has_merge"] and (r["uses_suffixes"] or r["drops_before_merge"])),
        "keyerror_risk": sum(1 for r in ok if r["keyerror_risk"]),
        "emit_ok": sum(1 for r in ok if r["has_emit_file"]),
    }


async def main():
    print("=" * 70)
    print("真实场景 A/B 测试: 千问跑发票整理 (复现用户 KeyError bug)")
    print("=" * 70)
    print(f"模型: qwen-plus | 场景: 真实 27 列发票数据 + 真实 attachments XML + 真实用户 prompt")

    # 用当前生产提示词,对比 qwen-plus vs claude
    print(f"\n{'='*70}\n模型对比: 同样新版提示词 + 真实场景\n{'='*70}")

    async def run_group_model(model_id: str, n: int = 5) -> list[dict]:
        print(f"\n--- {model_id} ---")
        results = []
        for i in range(n):
            r = await run_one(model_id, i + 1, version="NEW")
            results.append(r)
            if "error" in r:
                print(f"  Run {i+1}: ERROR")
            else:
                risk = "❌" if r["keyerror_risk"] else "✅"
                print(f"  Run {i+1}: lines={r['code_lines']:>3} merge={r['has_merge']} suffixes={r['uses_suffixes']} {risk}")
        return results

    qwen_results = await run_group_model("qwen-plus", n=5)
    claude_results = await run_group_model("claude-opus-4-7", n=5)

    sum_q = summarize(qwen_results, "qwen-plus")
    sum_c = summarize(claude_results, "claude-opus-4-7")

    print(f"\n{'='*70}\n模型对比汇总\n{'='*70}")
    print(f"{'指标':<25} {'qwen-plus':<20} {'claude-opus-4-7'}")
    print("-" * 65)
    print(f"{'merge 安全率':<22} {sum_q['safe_merge']}/{sum_q['merge_count']}               {sum_c['safe_merge']}/{sum_c['merge_count']}")
    print(f"{'KeyError 风险':<22} {sum_q['keyerror_risk']}/5 ({sum_q['keyerror_risk']*20}%)         {sum_c['keyerror_risk']}/5 ({sum_c['keyerror_risk']*20}%)")
    print(f"{'平均代码行':<22} {sum_q['avg_lines']:.1f}              {sum_c['avg_lines']:.1f}")

    old_results = []  # 复用变量,跳过 OLD vs NEW
    new_results = qwen_results

    sum_old = summarize(old_results, "旧版 (大改前)")
    sum_new = summarize(new_results, "新版 (当前生产)")

    print(f"\n\n{'='*70}\nA/B 对比汇总\n{'='*70}")
    print(f"{'指标':<25} {'旧版 A':<20} {'新版 B':<20} {'结论'}")
    print("-" * 80)
    print(f"{'平均代码行数':<22} {sum_old['avg_lines']:>5.1f}             {sum_new['avg_lines']:>5.1f}             {'新版 +' if sum_new['avg_lines'] > sum_old['avg_lines'] else '新版 -'}{abs(sum_new['avg_lines'] - sum_old['avg_lines']):.1f}")
    print(f"{'merge 使用次数':<22} {sum_old['merge_count']}/5               {sum_new['merge_count']}/5")
    print(f"{'merge 安全率':<22} {sum_old['safe_merge']}/{sum_old['merge_count']}               {sum_new['safe_merge']}/{sum_new['merge_count']}")
    print(f"{'KeyError 风险':<22} {sum_old['keyerror_risk']}/5 ({sum_old['keyerror_risk']*20}%)         {sum_new['keyerror_risk']}/5 ({sum_new['keyerror_risk']*20}%)         {'★ 新版退化' if sum_new['keyerror_risk'] > sum_old['keyerror_risk'] else '✅ 新版未退化'}")
    print(f"{'emit_file 触发率':<22} {sum_old['emit_ok']}/5               {sum_new['emit_ok']}/5")

    out_file = Path(__file__).parent / "poc_real_invoice_qwen_results.json"
    out_file.write_text(
        json.dumps({"OLD": old_results, "NEW": new_results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整代码: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
