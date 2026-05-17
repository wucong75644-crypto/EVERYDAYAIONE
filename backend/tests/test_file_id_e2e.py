"""
文件编号系统 E2E 测试

模拟完整对话流程：
1. 用户上传 3 个文件 → 注册编号
2. file_analyze 转 parquet → 编号路径升级
3. code_execute 沙盒 get_file 拿路径 → duckdb 查询
4. 多轮对话后回来找文件 → 编号仍有效
5. 工具参数翻译 → path/files 正确解析
6. 前端展示翻译 → 编号变回文件名
7. file_delete 用编号删除 → 翻译正确
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from services.agent.file_path_cache import FilePathCache, get_file_cache, _caches, _lock
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat_tool_mixin import _resolve_file_ids
from services.handlers.chat_generate_mixin import _translate_file_ids_for_display


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def workspace(tmp_path):
    """创建模拟 workspace，含 3 个文件"""
    ws = tmp_path / "workspace"
    ws.mkdir()

    # 文件1: 中文名 Excel（最常见的出错场景）
    f1 = ws / "4月销售分析-按订单商品明细.xlsx"
    f1.write_bytes(b"fake excel " * 100)

    # 文件2: 中文名 CSV
    f2 = ws / "产品库存表-2024年度.csv"
    f2.write_text("名称,数量\n产品A,100\n产品B,200\n", encoding="utf-8")

    # 文件3: 带特殊字符的文件名
    f3 = ws / "利润表（1-4月）汇总.xlsx"
    f3.write_bytes(b"fake excel profit " * 50)

    return ws


@pytest.fixture
def staging(tmp_path):
    """创建模拟 staging 目录"""
    s = tmp_path / "staging"
    s.mkdir()
    return s


@pytest.fixture
def conv_id():
    """每次测试用唯一的 conversation_id，避免全局缓存污染"""
    import uuid
    return f"test-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cache(conv_id, staging):
    """获取干净的会话级缓存"""
    c = get_file_cache(conv_id)
    c.set_staging_dir(str(staging))
    return c


# ============================================================
# 测试：完整对话流程
# ============================================================

class TestFullConversationFlow:
    """模拟完整对话：上传 → analyze → code_execute → 多轮回查"""

    def test_step1_upload_register(self, cache, workspace):
        """步骤1: 用户上传 3 个文件，注册编号"""
        files = list(workspace.iterdir())
        assert len(files) == 3

        ids = {}
        for f in sorted(files, key=lambda x: x.name):
            fid = cache.register(f.name, str(f))
            ids[f.name] = fid

        # 3 个文件 → 3 个不同编号
        assert len(set(ids.values())) == 3
        # 编号从 f1 开始
        assert "f1" in ids.values()
        assert "f2" in ids.values()
        assert "f3" in ids.values()

    def test_step2_workspace_prompt(self, cache, workspace):
        """步骤2: LLM 看到的提示词包含编号"""
        file_id_map = {}
        for f in sorted(workspace.iterdir(), key=lambda x: x.name):
            fid = cache.register(f.name, str(f))
            file_id_map[fid] = {
                "name": f.name,
                "size": f.stat().st_size,
            }

        prompt = ChatContextMixin._build_workspace_prompt([], file_id_map)

        # 提示词包含编号和文件名
        assert "f1" in prompt
        assert "f2" in prompt
        assert "f3" in prompt
        assert "file_analyze" in prompt
        # xlsx 文件有 file_analyze 提示
        assert 'file_analyze(path="' in prompt

    def test_step3_tool_param_translate(self, cache, workspace, conv_id):
        """步骤3: LLM 调 file_analyze(path='f1') → 翻译为绝对路径"""
        files = sorted(workspace.iterdir(), key=lambda x: x.name)
        fids = [cache.register(f.name, str(f)) for f in files]

        # 模拟 LLM 传 path="f1"
        args = {"path": "f1"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == str(files[0])
        assert args["path"].endswith(".xlsx") or args["path"].endswith(".csv")

        # 模拟 LLM 传 path="f2"
        args2 = {"path": "f2"}
        _resolve_file_ids(args2, conv_id)
        assert args2["path"] == str(files[1])

    def test_step4_analyze_upgrades_path(self, cache, workspace, staging):
        """步骤4: file_analyze 后编号路径升级为 parquet"""
        xlsx = sorted(workspace.iterdir(), key=lambda x: x.name)[0]
        fid = cache.register(xlsx.name, str(xlsx))

        # file_analyze 前：编号指向原始 xlsx
        assert cache.resolve(fid).endswith(".xlsx")

        # 模拟 file_analyze 产出 parquet
        parquet = staging / f"_cache_abc123_{xlsx.stem}.parquet"
        parquet.write_bytes(b"fake parquet data")

        # update_path: 把编号路径升级为 parquet
        cache.update_path(fid, str(parquet))

        # file_analyze 后：同一编号指向 parquet
        assert cache.resolve(fid) == str(parquet)
        assert cache.resolve(fid).endswith(".parquet")

        # display_name 不变（用户看到的还是原始文件名）
        assert cache.get_display_name(fid) == xlsx.name

    def test_step5_manifest_and_sandbox_get_file(self, cache, workspace, staging):
        """步骤5: write_manifest → 沙盒 get_file 正确读取"""
        # 注册 + analyze 升级
        files = sorted(workspace.iterdir(), key=lambda x: x.name)
        for f in files:
            fid = cache.register(f.name, str(f))
            if f.suffix in (".xlsx", ".xls"):
                parquet = staging / f"_cache_{f.stem}.parquet"
                parquet.write_bytes(b"fake parquet")
                cache.update_path(fid, str(parquet))

        # 写 manifest
        cache.write_manifest()
        manifest_path = staging / "_manifest.json"
        assert manifest_path.exists()

        # 读 manifest（模拟沙盒 get_file）
        with open(manifest_path) as mf:
            manifest = json.load(mf)

        # 所有编号都在 manifest 里
        assert "f1" in manifest
        assert "f2" in manifest
        assert "f3" in manifest

        # xlsx 文件的路径已升级为 parquet
        for fid, path in manifest.items():
            orig_file = files[int(fid[1:]) - 1]
            if orig_file.suffix in (".xlsx", ".xls"):
                assert path.endswith(".parquet"), f"{fid} should point to parquet, got {path}"
            else:
                # csv 文件保持原路径
                assert not path.endswith(".parquet")

    def test_step6_multi_turn_ids_survive(self, cache, workspace, conv_id):
        """步骤6: 多轮对话后编号仍有效"""
        files = sorted(workspace.iterdir(), key=lambda x: x.name)
        fids = [cache.register(f.name, str(f)) for f in files]

        # 模拟多轮对话（中间可能有其他操作）
        # 再次获取 cache（模拟新一轮请求）
        cache2 = get_file_cache(conv_id)

        # 所有编号仍可 resolve
        for i, fid in enumerate(fids):
            resolved = cache2.resolve(fid)
            assert resolved is not None, f"{fid} lost after multi-turn"
            assert resolved == str(files[i])

    def test_step7_file_delete_translate(self, cache, workspace, conv_id):
        """步骤7: file_delete 用编号删除 → 翻译正确"""
        files = sorted(workspace.iterdir(), key=lambda x: x.name)
        for f in files:
            cache.register(f.name, str(f))

        # LLM 传 files=["f1", "f3"]
        args = {"files": ["f1", "f3"]}
        _resolve_file_ids(args, conv_id)

        # 翻译为绝对路径
        assert len(args["files"]) == 2
        assert args["files"][0] == str(files[0])
        assert args["files"][1] == str(files[2])

    def test_step8_display_translate(self, cache, workspace, conv_id):
        """步骤8: 前端展示 → 编号翻译回文件名"""
        files = sorted(workspace.iterdir(), key=lambda x: x.name)
        for f in files:
            cache.register(f.name, str(f))

        # 模拟 tool_step input JSON
        raw = json.dumps({"path": "f1"})
        display = _translate_file_ids_for_display(raw, conv_id)

        parsed = json.loads(display)
        # 前端看到文件名，不是编号
        assert parsed["path"] == files[0].name
        assert "f1" not in display

    def test_step9_unknown_id_passthrough(self, cache, conv_id):
        """步骤9: 未注册编号不翻译，安全透传"""
        args = {"path": "f999"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == "f999"

    def test_step10_same_file_no_dup(self, cache, workspace):
        """步骤10: 同一文件重复注册不分配新编号"""
        f = sorted(workspace.iterdir())[0]

        id1 = cache.register(f.name, str(f))
        id2 = cache.register(f.name, str(f))
        id3 = cache.register("subdir/" + f.name, str(f))

        assert id1 == id2 == id3


class TestAnalyzeAndCodeExecuteFlow:
    """模拟 file_analyze → code_execute 完整链路"""

    def test_analyze_then_code_execute(self, cache, workspace, staging, conv_id):
        """file_analyze 产出 parquet → code_execute 里 get_file 拿到正确路径"""
        xlsx = workspace / "4月销售分析-按订单商品明细.xlsx"

        # 1. 用户上传注册
        fid = cache.register(xlsx.name, str(xlsx))
        assert fid == "f1"

        # 2. LLM 调 file_analyze(path="f1") → 翻译
        args = {"path": "f1"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == str(xlsx)

        # 3. file_analyze 成功，产出 parquet，升级路径
        parquet = staging / "_cache_abc_4月销售分析.parquet"
        parquet.write_bytes(b"PAR1" + b"\x00" * 100)  # fake parquet
        cache.update_path(fid, str(parquet))

        # 4. write_manifest
        cache.write_manifest()

        # 5. 验证 manifest 内容
        manifest = json.loads((staging / "_manifest.json").read_text())
        assert manifest["f1"] == str(parquet)

        # 6. 模拟沙盒 get_file('f1')
        path = manifest["f1"]
        assert os.path.exists(path)
        assert path.endswith(".parquet")

        # 7. LLM 第二次调工具引用同一编号
        args2 = {"path": "f1"}
        _resolve_file_ids(args2, conv_id)
        assert args2["path"] == str(parquet)  # 指向 parquet 不是 xlsx

    def test_multiple_files_analyze(self, cache, workspace, staging, conv_id):
        """3 个文件分别 analyze，编号不冲突"""
        files = sorted(workspace.iterdir(), key=lambda x: x.name)

        # 注册全部
        fids = [cache.register(f.name, str(f)) for f in files]
        assert fids == ["f1", "f2", "f3"]

        # 只对 xlsx 做 analyze（csv 不需要）
        for i, f in enumerate(files):
            if f.suffix in (".xlsx", ".xls"):
                parquet = staging / f"_cache_{i}_{f.stem}.parquet"
                parquet.write_bytes(b"fake parquet data")
                cache.update_path(fids[i], str(parquet))

        # 写 manifest
        cache.write_manifest()
        manifest = json.loads((staging / "_manifest.json").read_text())

        # xlsx → parquet, csv → 原路径
        for i, f in enumerate(files):
            fid = f"f{i+1}"
            if f.suffix in (".xlsx", ".xls"):
                assert manifest[fid].endswith(".parquet")
            else:
                assert manifest[fid] == str(f)

        # 全部编号可 resolve
        for fid in fids:
            assert cache.resolve(fid) is not None

    def test_code_execute_output_register(self, cache, workspace, staging, conv_id):
        """code_execute 产出新文件 → 注册新编号 → 后续可引用"""
        # 原始文件注册
        xlsx = sorted(workspace.iterdir())[0]
        cache.register(xlsx.name, str(xlsx))

        # 模拟 code_execute 产出 report.xlsx
        output_file = staging / "分析报告_2024Q1.xlsx"
        output_file.write_bytes(b"output excel")

        # 注册产出
        new_fid = cache.register(output_file.name, str(output_file))
        assert new_fid == "f2"

        # 后续工具可引用
        args = {"path": "f2"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == str(output_file)


class TestLRUAndTTLRobustness:
    """缓存鲁棒性：LRU 淘汰 + TTL 过期"""

    def test_lru_eviction_ids_survive(self):
        """_entries LRU 淘汰后编号仍可通过 _id_to_entry 查到"""
        cache = FilePathCache(max_entries=6)

        ids = []
        for i in range(20):
            fid = cache.register(f"file_{i}.xlsx", f"/ws/file_{i}.xlsx")
            ids.append(fid)

        # _entries 只保留最近的，但所有编号可 resolve
        for i, fid in enumerate(ids):
            resolved = cache.resolve(fid)
            assert resolved == f"/ws/file_{i}.xlsx", \
                f"{fid} should resolve to /ws/file_{i}.xlsx, got {resolved}"

    def test_ttl_expiry(self, staging):
        """TTL 过期后缓存重建"""
        import time as _time
        from services.agent.file_path_cache import _lock, _caches, _TTL_SECONDS

        conv = "test-ttl-expire-001"
        c = get_file_cache(conv)
        c.set_staging_dir(str(staging))
        c.register("old.xlsx", "/ws/old.xlsx")

        # 强制过期
        with _lock:
            ts, cache_obj = _caches[conv]
            _caches[conv] = (ts - _TTL_SECONDS - 1, cache_obj)

        # 过期后获取新缓存
        c2 = get_file_cache(conv)
        assert c2.resolve("f1") is None  # 新缓存是空的


class TestEdgeCases:
    """边界场景"""

    def test_update_path_nonexistent_id(self, cache):
        """update_path 对不存在的编号不报错"""
        cache.update_path("f999", "/some/path")
        # 不崩溃即可

    def test_empty_manifest_not_written(self, cache, staging):
        """无注册文件时不写 manifest"""
        cache.write_manifest()
        assert not (staging / "_manifest.json").exists()

    def test_manifest_updates_on_rewrite(self, cache, workspace, staging):
        """每次 write_manifest 是全量覆盖"""
        f = sorted(workspace.iterdir())[0]

        # 第一次写
        cache.register(f.name, str(f))
        cache.write_manifest()
        m1 = json.loads((staging / "_manifest.json").read_text())
        assert len(m1) == 1

        # 注册更多文件后再写
        for f2 in sorted(workspace.iterdir())[1:]:
            cache.register(f2.name, str(f2))
        cache.write_manifest()
        m2 = json.loads((staging / "_manifest.json").read_text())
        assert len(m2) == 3

    def test_chinese_filename_in_manifest(self, cache, staging):
        """中文文件名在 manifest 中正确存储"""
        cache.register("销售报表（含退款）.xlsx", "/ws/销售报表（含退款）.xlsx")
        cache.write_manifest()

        raw = (staging / "_manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(raw)
        assert "销售报表（含退款）.xlsx" in manifest["f1"]

    def test_display_translate_preserves_non_id_fields(self, cache, conv_id):
        """前端翻译不影响非编号字段"""
        cache.register("test.xlsx", "/ws/test.xlsx")

        raw = json.dumps({
            "path": "f1",
            "keyword": "formula",
            "count": 42,
        })
        display = _translate_file_ids_for_display(raw, conv_id)
        parsed = json.loads(display)

        assert parsed["path"] == "test.xlsx"
        assert parsed["keyword"] == "formula"  # 不被翻译
        assert parsed["count"] == 42

    def test_backward_compat_filename_resolve(self, cache, workspace):
        """向后兼容：用文件名仍能 resolve"""
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))

        # 用文件名查（旧方式）
        assert cache.resolve(f.name) == str(f)
        # 用编号查（新方式）
        assert cache.resolve("f1") == str(f)


class TestSandboxGetFile:
    """沙盒 get_file 函数的真实模拟"""

    def _build_get_file(self, staging):
        """构建和 sandbox_worker 里一样的 get_file 函数"""
        import builtins
        manifest_path = str(staging / "_manifest.json")

        def get_file(file_id: str) -> str:
            try:
                with builtins.open(manifest_path, "r", encoding="utf-8") as mf:
                    manifest = json.load(mf)
            except FileNotFoundError:
                raise FileNotFoundError(
                    "文件编号注册表不存在。请先调用 file_analyze 或 file_search 注册文件。"
                )
            path = manifest.get(file_id)
            if not path:
                available = list(manifest.keys())
                raise FileNotFoundError(
                    f"编号 '{file_id}' 不存在。可用编号: {available}"
                )
            return path

        return get_file

    def test_get_file_success(self, cache, workspace, staging):
        """沙盒 get_file 正常返回路径"""
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        assert get_file("f1") == str(f)

    def test_get_file_after_analyze(self, cache, workspace, staging):
        """file_analyze 后 get_file 返回 parquet 路径"""
        f = sorted(workspace.iterdir())[0]
        fid = cache.register(f.name, str(f))

        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet data")
        cache.update_path(fid, str(parquet))
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        path = get_file("f1")
        assert path == str(parquet)
        assert path.endswith(".parquet")

    def test_get_file_missing_manifest(self, staging):
        """manifest 不存在时报错清晰"""
        get_file = self._build_get_file(staging)
        with pytest.raises(FileNotFoundError, match="注册表不存在"):
            get_file("f1")

    def test_get_file_unknown_id(self, cache, workspace, staging):
        """编号不存在时报错并列出可用编号"""
        cache.register("a.xlsx", "/ws/a.xlsx")
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        with pytest.raises(FileNotFoundError, match="可用编号.*f1"):
            get_file("f99")

    def test_manifest_updates_between_calls(self, cache, workspace, staging):
        """模拟 kernel_worker 长连接：两次 code_execute 之间 manifest 更新"""
        get_file = self._build_get_file(staging)

        # 第一次 code_execute：只有 f1
        f1 = sorted(workspace.iterdir())[0]
        cache.register(f1.name, str(f1))
        cache.write_manifest()
        assert get_file("f1") == str(f1)

        # file_analyze 产出 → 注册新编号 + update_path
        parquet = staging / "_cache_new.parquet"
        parquet.write_bytes(b"parquet")
        cache.update_path("f1", str(parquet))
        f2 = sorted(workspace.iterdir())[1]
        cache.register(f2.name, str(f2))

        # 第二次 code_execute 前重写 manifest
        cache.write_manifest()

        # 沙盒能读到新编号 + 更新后的路径
        assert get_file("f1") == str(parquet)  # 路径已升级
        assert get_file("f2") == str(f2)       # 新编号可用


class TestFileRefIntegration:
    """FileRef + file_id 写回"""

    def test_sandbox_ref_with_file_id(self):
        """FileRef 有 file_id 时 sandbox_ref 返回编号"""
        from services.agent.tool_output import FileRef, ColumnMeta

        fr = FileRef(
            path="/staging/trade.parquet",
            filename="trade.parquet",
            format="parquet",
            row_count=100,
            size_bytes=1024,
            columns=[ColumnMeta("id", "integer")],
            file_id="f5",
        )
        assert fr.sandbox_ref == "f5"

    def test_sandbox_ref_without_file_id(self):
        """FileRef 无 file_id 时降级旧格式"""
        from services.agent.tool_output import FileRef, ColumnMeta

        fr = FileRef(
            path="/staging/trade.parquet",
            filename="trade.parquet",
            format="parquet",
            row_count=100,
            size_bytes=1024,
            columns=[ColumnMeta("id", "integer")],
        )
        assert fr.sandbox_ref == "STAGING_DIR + '/trade.parquet'"

    def test_file_id_writeback_frozen(self):
        """frozen dataclass 通过 object.__setattr__ 写回 file_id"""
        from services.agent.tool_output import FileRef, ColumnMeta

        fr = FileRef(
            path="/staging/trade.parquet",
            filename="trade.parquet",
            format="parquet",
            row_count=100,
            size_bytes=1024,
            columns=[ColumnMeta("id", "integer")],
        )
        assert fr.file_id == ""

        # 模拟 _register_staging_files 写回
        object.__setattr__(fr, "file_id", "f3")
        assert fr.file_id == "f3"
        assert fr.sandbox_ref == "f3"


class TestFileSearchThenCodeExecute:
    """file_search 后直接 code_execute（不经过 file_analyze）"""

    def test_search_register_then_sandbox(self, cache, workspace, staging, conv_id):
        """搜到文件 → 注册 → 沙盒用 get_file 读原始文件"""
        csv = workspace / "产品库存表-2024年度.csv"

        # file_search 注册
        fid = cache.register(csv.name, str(csv))

        # 不做 file_analyze（csv 可以直接 pd.read_csv）
        # get_file 返回原始 csv 路径
        assert cache.resolve(fid) == str(csv)

        # 工具参数翻译
        args = {"path": fid}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == str(csv)

        # 沙盒 manifest 也正确
        cache.write_manifest()
        manifest = json.loads((staging / "_manifest.json").read_text())
        assert manifest[fid] == str(csv)


class TestResolveFileIdsIsolation:
    """_resolve_file_ids 对无关工具参数不干扰"""

    def test_erp_agent_args_untouched(self, cache, conv_id):
        """erp_agent 参数没有 path/files，不受影响"""
        cache.register("test.xlsx", "/ws/test.xlsx")

        args = {
            "query": "查5月订单",
            "time_range": "2024-05-01~2024-05-31",
            "platform": "taobao",
        }
        original = args.copy()
        _resolve_file_ids(args, conv_id)
        assert args == original

    def test_web_search_args_untouched(self, cache, conv_id):
        """web_search 参数不受影响"""
        args = {"query": "天气预报", "format": "full"}
        original = args.copy()
        _resolve_file_ids(args, conv_id)
        assert args == original

    def test_code_execute_args_untouched(self, cache, conv_id):
        """code_execute 参数（code/description）不受影响"""
        args = {
            "code": "path = get_file('f1')\nprint(path)",
            "description": "读取 f1 文件",
        }
        original = args.copy()
        _resolve_file_ids(args, conv_id)
        assert args == original

    def test_path_with_real_path_still_works(self, cache, conv_id):
        """path 传真实路径（非编号）也能 resolve"""
        cache.register("test.xlsx", "/ws/test.xlsx")

        # LLM 传了文件名而不是编号（向后兼容）
        args = {"path": "test.xlsx"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == "/ws/test.xlsx"

    def test_files_mixed_ids_and_names(self, cache, conv_id):
        """files 数组混合编号和文件名"""
        cache.register("a.xlsx", "/ws/a.xlsx")
        cache.register("b.csv", "/ws/b.csv")

        args = {"files": ["f1", "b.csv", "unknown.txt"]}
        _resolve_file_ids(args, conv_id)

        assert args["files"][0] == "/ws/a.xlsx"      # 编号翻译
        assert args["files"][1] == "/ws/b.csv"        # 文件名翻译
        assert args["files"][2] == "unknown.txt"      # 未注册透传


class TestConcurrency:
    """并发注册不冲突"""

    def test_different_conversations_independent(self):
        """不同对话的编号互相独立"""
        c1 = get_file_cache("conv-concurrent-1")
        c2 = get_file_cache("conv-concurrent-2")

        id1 = c1.register("file.xlsx", "/ws1/file.xlsx")
        id2 = c2.register("file.xlsx", "/ws2/file.xlsx")

        # 相同编号，不同路径
        assert id1 == "f1"
        assert id2 == "f1"
        assert c1.resolve("f1") == "/ws1/file.xlsx"
        assert c2.resolve("f1") == "/ws2/file.xlsx"

    def test_sequential_register_no_gap(self, cache):
        """连续注册编号连续递增"""
        ids = []
        for i in range(10):
            fid = cache.register(f"f{i}.txt", f"/ws/f{i}.txt")
            ids.append(fid)
        assert ids == [f"f{i}" for i in range(1, 11)]
