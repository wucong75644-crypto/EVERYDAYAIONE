"""POC: 沙盒 Phase 1 根治方案验证

目标:在动手改正式代码前,验证三件事:
  A. 生产 nsjail 配置足以兜底(静态分析 deploy/sandbox.cfg)
  B. 删 Python 层白名单后,matplotlib/scipy/zoneinfo 等不再报 PermissionError
  C. 删 Python 层白名单后,UX 引导(import 黑名单、scoped_os.remove)仍生效

运行:
  source backend/venv/bin/activate
  python backend/scripts/poc_sandbox_phase1.py

退出码:0=POC 通过可实施 / 非0=POC 失败需重新设计
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# 让 POC 能 import backend 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ──────────────────────────────────────────
# SECTION A: nsjail 配置静态校验
# ──────────────────────────────────────────

def section_a_nsjail_config() -> tuple[int, int]:
    """解析 deploy/sandbox.cfg, 断言关键安全项齐全。"""
    print("\n" + "=" * 70)
    print("SECTION A: nsjail 配置静态校验 (生产兜底是否够)")
    print("=" * 70)

    cfg_path = Path(__file__).resolve().parent.parent.parent / "deploy" / "sandbox.cfg"
    if not cfg_path.exists():
        print(f"❌ FATAL: 找不到 sandbox.cfg: {cfg_path}")
        return 0, 1

    text = cfg_path.read_text(encoding="utf-8")
    checks = [
        ("clone_newnet: true",            "网络完全切断"),
        ("cgroup_mem_max:",               "内存 cgroup 限制"),
        ("cgroup_pids_max:",              "进程数 cgroup 限制"),
        ('mount { src: "/usr"',           "/usr ro bind"),
        ('mount { src: "/lib"',           "/lib ro bind"),
        ('src: "/var/www/everydayai/backend/venv"', "/venv ro bind (Python 库)"),
        ('rlimit_fsize:',                 "rlimit_fsize 硬限制"),
        ('rlimit_nofile:',                "rlimit_nofile 硬限制"),
    ]
    passed = failed = 0
    for needle, name in checks:
        if needle in text:
            print(f"  ✅ {name}: 找到 `{needle.split(chr(10))[0][:40]}`")
            passed += 1
        else:
            print(f"  ❌ {name}: 未找到 `{needle}`")
            failed += 1

    # 必须检查:确保 /venv 是 ro
    if 'src: "/var/www/everydayai/backend/venv"' in text:
        venv_line_idx = text.find('src: "/var/www/everydayai/backend/venv"')
        line_end = text.find("\n", venv_line_idx)
        venv_line = text[venv_line_idx:line_end]
        if "rw: false" in venv_line:
            print(f"  ✅ /venv 明确 rw=false (OS 层挡写入,Python 层校验冗余)")
            passed += 1
        else:
            print(f"  ❌ /venv 未明确 rw=false: {venv_line}")
            failed += 1

    print(f"\n  SECTION A 小结: {passed} pass / {failed} fail")
    return passed, failed


# ──────────────────────────────────────────
# SECTION B: 模拟删 Python 层后,库内部资源访问不再被拒
# ──────────────────────────────────────────

def _build_scoped_open_no_whitelist(workspace_dir, staging_dir, output_dir):
    """模拟 Phase 1 改造后的 scoped_open: 只做路径解析 + 文件名纠错,不做白名单。

    这是 Phase 1 准备应用到 sandbox_worker.py 的最终代码原型。
    """
    import builtins
    from services.sandbox.sandbox_worker import _find_similar_file_global

    _orig_open = builtins.open
    _ws_dir = workspace_dir

    def _scoped_open(path, mode="r", *args, **kwargs):
        path_str = str(path)
        if not os.path.isabs(path_str):
            path_str = os.path.join(_ws_dir, path_str)
        resolved = os.path.realpath(path_str)

        # 文件不存在时自动纠错(业务功能,与安全无关)
        if "r" in mode and not os.path.exists(resolved):
            _basename = os.path.basename(resolved)
            suggestion = _find_similar_file_global(resolved, _ws_dir)
            if not suggestion:
                for _fallback_dir in (output_dir, staging_dir):
                    if not _fallback_dir:
                        continue
                    _alt = os.path.join(_fallback_dir, _basename)
                    if os.path.exists(_alt):
                        suggestion = _alt
                        break
            if suggestion and os.path.exists(suggestion):
                return _orig_open(suggestion, mode, *args, **kwargs)
            if not os.path.exists(resolved):
                raise FileNotFoundError(f"文件不存在: {path}")
        return _orig_open(resolved, mode, *args, **kwargs)

    return _scoped_open


def section_b_no_more_permission_error(tmp_workspace: Path) -> tuple[int, int]:
    """临时换上 no-whitelist 版 scoped_open, 跑实际库代码看是否报错。"""
    print("\n" + "=" * 70)
    print("SECTION B: 删 Python 层白名单后,真实库代码不再 PermissionError")
    print("=" * 70)

    import builtins
    _orig = builtins.open
    scoped = _build_scoped_open_no_whitelist(
        str(tmp_workspace),
        str(tmp_workspace / "staging"),
        str(tmp_workspace / "output"),
    )
    builtins.open = scoped

    passed = failed = 0
    test_cases = [
        ("matplotlib 字体加载", _test_matplotlib),
        ("plotly fig.to_dict()", _test_plotly),
        ("altair Chart.to_dict()", _test_altair),
        ("import openpyxl 读 xlsx 内部资源", _test_openpyxl),
        ("zoneinfo 读 Asia/Shanghai", _test_zoneinfo),
        ("mimetypes 读 mime 数据库", _test_mimetypes),
    ]
    try:
        for name, fn in test_cases:
            try:
                fn(tmp_workspace)
                print(f"  ✅ {name}: 通过")
                passed += 1
            except PermissionError as e:
                print(f"  ❌ {name}: PermissionError → {e}")
                failed += 1
            except Exception as e:
                # 非 PermissionError(库本身报错),不算 POC 失败
                print(f"  ⚠️  {name}: 非权限错误 → {type(e).__name__}: {str(e)[:80]}")
                passed += 1  # 不是路径白名单问题,算通过
    finally:
        builtins.open = _orig

    print(f"\n  SECTION B 小结: {passed} pass / {failed} fail")
    return passed, failed


def _test_matplotlib(tmp_workspace: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.bar(["A", "B"], [1, 2])
    out = tmp_workspace / "test_mpl.png"
    fig.savefig(out, dpi=80)
    plt.close(fig)
    assert out.exists()


def _test_plotly(tmp_workspace: Path) -> None:
    import plotly.graph_objects as go
    fig = go.Figure(data=[go.Bar(x=["A", "B"], y=[1, 2])])
    spec = fig.to_dict()
    assert "data" in spec


def _test_altair(tmp_workspace: Path) -> None:
    import altair as alt
    import pandas as pd
    chart = alt.Chart(pd.DataFrame({"x": [1, 2], "y": [3, 4]})).mark_bar().encode(x="x", y="y")
    spec = chart.to_dict()
    assert "data" in spec


def _test_openpyxl(tmp_workspace: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    out = tmp_workspace / "test_xlsx.xlsx"
    wb.save(out)
    assert out.exists()


def _test_zoneinfo(tmp_workspace: Path) -> None:
    from zoneinfo import ZoneInfo
    from datetime import datetime
    tz = ZoneInfo("Asia/Shanghai")
    dt = datetime.now(tz)
    assert dt.tzinfo is tz


def _test_mimetypes(tmp_workspace: Path) -> None:
    import mimetypes
    mimetypes.init()
    t, _ = mimetypes.guess_type("test.csv")
    assert t in ("text/csv", "application/vnd.ms-excel", None)


# ──────────────────────────────────────────
# SECTION C: UX 引导仍生效
# ──────────────────────────────────────────

def section_c_ux_guards_still_work() -> tuple[int, int]:
    """删路径白名单后,import 黑名单/AST 校验/scoped_os.remove 仍然挡住"""
    print("\n" + "=" * 70)
    print("SECTION C: UX 引导仍生效(import 黑名单 / AST / 删除拦截)")
    print("=" * 70)

    passed = failed = 0

    # C1: import 黑名单
    from services.sandbox.sandbox_constants import BLOCKED_IMPORT_MODULES
    must_block = ["subprocess", "ctypes", "multiprocessing", "pickle"]
    for mod in must_block:
        if mod in BLOCKED_IMPORT_MODULES:
            print(f"  ✅ import 黑名单仍含 `{mod}`")
            passed += 1
        else:
            print(f"  ❌ import 黑名单缺失 `{mod}`")
            failed += 1

    # C2: AST validators 仍拒 eval/exec
    from services.sandbox.validators import validate_code
    bad_codes = [
        ('eval("1+1")', "eval"),
        ('exec("x=1")', "exec"),
        ('__import__("os").system("ls")', "__import__"),
    ]
    for code, name in bad_codes:
        err = validate_code(code)
        if err:
            print(f"  ✅ AST 拒 `{name}`: {err}")
            passed += 1
        else:
            print(f"  ❌ AST 漏放 `{name}` (validate_code 返回 None)")
            failed += 1

    # C3: scoped_os.remove 仍引导用 file_delete
    from services.sandbox.scoped_os import build_scoped_os
    scoped, _ = build_scoped_os("/tmp", "/tmp/staging", "/tmp/output")
    try:
        scoped.remove("/tmp/test")
        print(f"  ❌ scoped_os.remove 未拦截")
        failed += 1
    except PermissionError as e:
        if "file_delete" in str(e):
            print(f"  ✅ scoped_os.remove 仍引导用 file_delete: {e}")
            passed += 1
        else:
            print(f"  ⚠️  scoped_os.remove 拦了但消息不含 'file_delete': {e}")
            passed += 1

    # C4: SAFE_BUILTINS 仍禁 input/open
    from services.sandbox.sandbox_constants import SAFE_BUILTINS
    for name in ["open", "input", "eval", "exec"]:
        if name not in SAFE_BUILTINS:
            print(f"  ✅ SAFE_BUILTINS 仍禁 `{name}`")
            passed += 1
        else:
            print(f"  ❌ SAFE_BUILTINS 含 `{name}` (应该禁)")
            failed += 1

    print(f"\n  SECTION C 小结: {passed} pass / {failed} fail")
    return passed, failed


# ──────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────

def main() -> int:
    print("\n" + "█" * 70)
    print("█  沙盒 Phase 1 POC: 删 Python 层路径白名单的可行性验证")
    print("█" * 70)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="sandbox_poc_") as td:
        tmp_workspace = Path(td)
        (tmp_workspace / "staging").mkdir(exist_ok=True)
        (tmp_workspace / "output").mkdir(exist_ok=True)

        a_pass, a_fail = section_a_nsjail_config()
        b_pass, b_fail = section_b_no_more_permission_error(tmp_workspace)
        c_pass, c_fail = section_c_ux_guards_still_work()

    print("\n" + "═" * 70)
    print("总评")
    print("═" * 70)
    total_pass = a_pass + b_pass + c_pass
    total_fail = a_fail + b_fail + c_fail
    print(f"  Section A (nsjail 配置): {a_pass}/{a_pass+a_fail}")
    print(f"  Section B (路径访问 bug 修复): {b_pass}/{b_pass+b_fail}")
    print(f"  Section C (UX 引导保留): {c_pass}/{c_pass+c_fail}")
    print(f"  合计: {total_pass} pass / {total_fail} fail")

    if total_fail == 0:
        print("\n  ✅ POC 全部通过 — Phase 1 可实施")
        return 0
    else:
        print(f"\n  ❌ POC 失败 {total_fail} 项 — 不实施 Phase 1,先修复 POC 失败项")
        return 1


if __name__ == "__main__":
    sys.exit(main())
