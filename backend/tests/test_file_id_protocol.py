"""Phase 1 单测：compute_fid 哈希函数 + attachments XML 注入 <id> 字段。

验证：
- 幂等：同 (org_id, path) 同 fid
- 跨 org 隔离：不同 org 同 path 不同 fid
- 单 org 内同 path 同 fid
- 格式：fid_ + 8 位 hex
- 冲突率：单 org 1000 文件冲突极低
- XML 渲染：包含 <id> 标签
- workspace_prompt 渲染：包含 [fid_xxx] 前缀
- 附件使用规则文字注入
"""

from services.agent.file_id import (
    compute_fid,
    is_valid_fid,
    resolve_fid_to_workspace,
)
from services.agent.file_path_cache import FilePathCache
from services.handlers.chat_context.attachments import (
    build_workspace_prompt,
    format_attachments,
)


# ─────────── compute_fid ───────────

class TestComputeFid:
    def test_format(self):
        fid = compute_fid("org_1", "上传/2026-06/x.xlsx")
        assert fid.startswith("fid_")
        assert len(fid) == 12
        assert is_valid_fid(fid)

    def test_idempotent(self):
        """同 (org, path) 永远得同 fid。"""
        path = "已整理表格/饶/4月销售.xlsx"
        assert compute_fid("org_a", path) == compute_fid("org_a", path)

    def test_org_isolation(self):
        """不同 org 同 path → 不同 fid。"""
        path = "上传/2026-06/同名.xlsx"
        assert compute_fid("org_a", path) != compute_fid("org_b", path)

    def test_path_difference(self):
        """同 org 不同 path → 不同 fid。"""
        assert compute_fid("org", "a.xlsx") != compute_fid("org", "b.xlsx")

    def test_none_org(self):
        """org_id=None 视为 ""，不抛错。"""
        fid = compute_fid(None, "x.xlsx")
        assert is_valid_fid(fid)

    def test_chinese_path(self):
        """中文路径正常哈希。"""
        fid = compute_fid("org", "已整理表格/饶/4月销售-按订单商品明细.xlsx")
        assert is_valid_fid(fid)

    def test_no_collisions_in_1k_files(self):
        """单 org 1000 文件冲突率应为 0。"""
        seen = set()
        for i in range(1000):
            fid = compute_fid("org", f"file_{i}.xlsx")
            assert fid not in seen, f"碰撞: {fid} at i={i}"
            seen.add(fid)
        assert len(seen) == 1000

    def test_is_valid_fid(self):
        assert is_valid_fid("fid_a3f2b1c9")
        assert not is_valid_fid("fid_XYZ")  # 大写
        assert not is_valid_fid("FID_a3f2b1c9")  # 前缀大写
        assert not is_valid_fid("fid_a3f2b1")  # 太短
        assert not is_valid_fid("fid_a3f2b1c9d")  # 太长
        assert not is_valid_fid("")
        assert not is_valid_fid("xxx")


# ─────────── format_attachments ───────────

def _file(name, *, wp=None, size=1024):
    return {
        "name": name,
        "workspace_path": wp or f"上传/2026-06/{name}",
        "size": size,
        "mime_type": "application/octet-stream",
    }


class TestFormatAttachmentsWithId:
    def test_xml_contains_id_tag(self):
        xml = format_attachments(
            [_file("4月销售.xlsx", wp="已整理表格/饶/4月销售.xlsx")],
            conversation_id="conv-1",
            org_id="org_test",
        )
        expected_fid = compute_fid("org_test", "已整理表格/饶/4月销售.xlsx")
        assert f"<id>{expected_fid}</id>" in xml

    def test_xml_contains_name_path_id_all_three(self):
        xml = format_attachments(
            [_file("a.xlsx", wp="x/a.xlsx")],
            org_id="org_x",
        )
        assert "<id>" in xml
        assert "<name>" in xml
        assert "<path>" in xml

    def test_xml_contains_usage_rules(self):
        """附件使用规则文字必须注入（让 LLM 知道分工）。"""
        xml = format_attachments(
            [_file("a.xlsx")],
            org_id="org",
        )
        assert "【附件使用规则】" in xml
        assert "file_id" in xml
        assert "<name>" in xml
        assert "<path>" in xml

    def test_same_org_same_path_same_id_across_calls(self):
        """两次调用同 org 同 path → 同 fid（确定性哈希）。"""
        a = format_attachments([_file("x.xlsx", wp="p/x.xlsx")], org_id="o")
        b = format_attachments([_file("x.xlsx", wp="p/x.xlsx")], org_id="o")
        # 提取 <id> 内容比较
        import re
        ids_a = re.findall(r"<id>(fid_\w+)</id>", a)
        ids_b = re.findall(r"<id>(fid_\w+)</id>", b)
        assert ids_a == ids_b

    def test_org_isolation_in_xml(self):
        """不同 org 同 path → XML 里 fid 不同。"""
        import re
        a = format_attachments([_file("x.xlsx", wp="p/x.xlsx")], org_id="org_a")
        b = format_attachments([_file("x.xlsx", wp="p/x.xlsx")], org_id="org_b")
        ids_a = re.findall(r"<id>(fid_\w+)</id>", a)
        ids_b = re.findall(r"<id>(fid_\w+)</id>", b)
        assert ids_a != ids_b

    def test_org_none_does_not_crash(self):
        """旧调用方未传 org_id → 不抛错。"""
        xml = format_attachments([_file("x.xlsx")], conversation_id="c")
        assert "<id>" in xml

    def test_no_files_returns_empty(self):
        assert format_attachments([]) == ""


# ─────────── build_workspace_prompt ───────────

class TestResolveFidToWorkspace:
    def test_returns_abs_path_when_fid_matches(self):
        cache = FilePathCache()
        wp = "已整理表格/饶/4月销售.xlsx"
        abs_path = "/workspace/u1/已整理表格/饶/4月销售.xlsx"
        cache.register(wp, workspace=abs_path)

        fid = compute_fid("org_x", wp)
        resolved = resolve_fid_to_workspace(fid, "org_x", cache)
        assert resolved == abs_path

    def test_returns_none_for_unknown_fid(self):
        cache = FilePathCache()
        cache.register("a.xlsx", workspace="/ws/a.xlsx")
        assert resolve_fid_to_workspace("fid_00000000", "org", cache) is None

    def test_returns_none_for_wrong_org(self):
        cache = FilePathCache()
        wp = "a.xlsx"
        cache.register(wp, workspace="/ws/a.xlsx")
        fid = compute_fid("org_a", wp)
        # 用错 org 查 → 找不到
        assert resolve_fid_to_workspace(fid, "org_b", cache) is None

    def test_returns_none_for_invalid_format(self):
        cache = FilePathCache()
        cache.register("a.xlsx", workspace="/ws/a.xlsx")
        assert resolve_fid_to_workspace("not_a_fid", "org", cache) is None
        assert resolve_fid_to_workspace("", "org", cache) is None

    def test_returns_none_for_empty_cache(self):
        cache = FilePathCache()
        assert resolve_fid_to_workspace("fid_abcd1234", "org", cache) is None


class TestFileIdSchemaConsistency:
    """验证工具 schema 真的暴露了 file_id 字段（pattern 锁死格式）。"""

    def test_file_analyze_schema_has_file_id(self):
        from config.file_tools import build_file_tools
        tools = build_file_tools()
        analyze = next(
            t for t in tools if t["function"]["name"] == "file_analyze"
        )
        props = analyze["function"]["parameters"]["properties"]
        assert "file_id" in props
        assert props["file_id"].get("pattern") == r"^fid_[a-z0-9]{8}$"
        # path 字段保留作为老协议兼容
        assert "path" in props

    def test_file_delete_schema_has_file_ids(self):
        from config.file_tools import build_file_tools
        tools = build_file_tools()
        delete = next(
            t for t in tools if t["function"]["name"] == "file_delete"
        )
        props = delete["function"]["parameters"]["properties"]
        assert "file_ids" in props
        items = props["file_ids"]["items"]
        assert items.get("pattern") == r"^fid_[a-z0-9]{8}$"


class TestBuildWorkspacePromptWithId:
    def test_contains_fid_prefix(self):
        text = build_workspace_prompt(
            [_file("4月销售.xlsx", wp="已整理表格/饶/4月销售.xlsx")],
            conversation_id="c",
            org_id="org_test",
        )
        expected_fid = compute_fid("org_test", "已整理表格/饶/4月销售.xlsx")
        assert f"[{expected_fid}]" in text
        assert "4月销售.xlsx" in text

    def test_no_files_returns_empty(self):
        assert build_workspace_prompt([]) == ""

    def test_org_none_does_not_crash(self):
        text = build_workspace_prompt([_file("a.xlsx")])
        assert "[fid_" in text
