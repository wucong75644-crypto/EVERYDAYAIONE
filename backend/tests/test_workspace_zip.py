"""
工作区批量下载 ZIP endpoint 单元测试

覆盖：
- _collect_zip_targets：单文件 / 文件夹递归 / 混合 / 不存在 / 越权 / 上限
- _resolve_archive_name：单文件夹 / 多文件命名
- ZIP 流式输出：UTF-8 文件名、内容正确性
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from urllib.parse import quote

from api.routes.file_download import (
    _ZIP_MAX_FILES,
    _ZIP_MAX_TOTAL_BYTES,
    _ascii_fallback,
    _collect_zip_targets,
    _resolve_archive_name,
)
from services.file_executor import FileExecutor


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """构造一个含中文目录、子目录、混合文件的临时工作区"""
    (tmp_path / "下载").mkdir()
    (tmp_path / "下载" / "图片.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "下载" / "report.xlsx").write_bytes(b"PK fake xlsx")
    (tmp_path / "下载" / "子文件夹").mkdir()
    (tmp_path / "下载" / "子文件夹" / "数据.csv").write_text("a,b\n1,2", encoding="utf-8")
    (tmp_path / "下载" / "子文件夹" / "nested.txt").write_text("nested", encoding="utf-8")
    return tmp_path


@pytest.fixture
def executor(workspace: Path) -> FileExecutor:
    return FileExecutor(workspace_root=str(workspace))


# ============================================================
# _resolve_archive_name
# ============================================================


class TestResolveArchiveName:
    def test_single_folder_uses_folder_name(self) -> None:
        assert _resolve_archive_name(["下载/子文件夹"]) == "子文件夹.zip"

    def test_single_file_uses_basename(self) -> None:
        assert _resolve_archive_name(["下载/图片.png"]) == "图片.png.zip"

    def test_multiple_paths_uses_timestamp(self) -> None:
        name = _resolve_archive_name(["a.png", "b.xlsx"])
        assert name.startswith("workspace-")
        assert name.endswith(".zip")

    def test_trailing_slash_handled(self) -> None:
        assert _resolve_archive_name(["下载/子文件夹/"]) == "子文件夹.zip"


# ============================================================
# _ascii_fallback — RFC 6266 ASCII filename 兜底（防中文崩溃 + header 注入）
# ============================================================


class TestAsciiFallback:
    def test_pure_ascii_passes_through(self) -> None:
        assert _ascii_fallback("workspace-20260622.zip") == "workspace-20260622.zip"

    def test_chinese_replaced_with_underscore(self) -> None:
        out = _ascii_fallback("子文件夹.zip")
        # 必须能 latin-1 编码（HTTP header 硬性要求）
        out.encode("latin-1")
        assert out.endswith(".zip")
        assert "子" not in out

    def test_crlf_injection_neutralized(self) -> None:
        out = _ascii_fallback('evil\r\nSet-Cookie: x=1.zip')
        assert "\r" not in out and "\n" not in out

    def test_double_quote_escaped(self) -> None:
        out = _ascii_fallback('a"b.zip')
        assert '"' not in out

    def test_backslash_escaped(self) -> None:
        out = _ascii_fallback("a\\b.zip")
        assert "\\" not in out

    def test_non_zip_extension_fallback(self) -> None:
        """没有 .zip 后缀时直接兜底 download.zip"""
        assert _ascii_fallback("子文件夹") == "download.zip"

    def test_full_chinese_name_produces_valid_header(self) -> None:
        """端到端：中文 archive_name → 生成的 Content-Disposition 必须能 latin-1 编码"""
        archive_name = "子文件夹.zip"
        ascii_name = _ascii_fallback(archive_name)
        encoded_name = quote(archive_name)
        header_value = (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
        )
        # ASGI / uvicorn 用 latin-1 编码 header；不抛异常即通过
        header_value.encode("latin-1")


# ============================================================
# _collect_zip_targets — 基础路径
# ============================================================


class TestCollectTargets:
    def test_single_file(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(executor, ["下载/图片.png"])
        assert len(targets) == 1
        assert targets[0][1] == "图片.png"
        assert errors == []

    def test_folder_recurses(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(executor, ["下载/子文件夹"])
        arcnames = sorted(arc for _, arc in targets)
        assert arcnames == ["子文件夹/nested.txt", "子文件夹/数据.csv"]
        assert errors == []

    def test_mixed_files_and_folder(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(
            executor,
            ["下载/图片.png", "下载/子文件夹"],
        )
        arcs = sorted(arc for _, arc in targets)
        assert "图片.png" in arcs
        assert "子文件夹/nested.txt" in arcs
        assert "子文件夹/数据.csv" in arcs
        assert len(targets) == 3
        assert errors == []

    def test_missing_file_reported_in_errors(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(executor, ["下载/不存在.png"])
        assert targets == []
        assert len(errors) == 1
        assert "不存在" in errors[0]

    def test_partial_missing_does_not_fail(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(
            executor,
            ["下载/图片.png", "下载/不存在.png"],
        )
        assert len(targets) == 1
        assert len(errors) == 1


# ============================================================
# _collect_zip_targets — 限额
# ============================================================


class TestLimits:
    def test_too_many_files_raises(self, tmp_path: Path) -> None:
        # 构造 _ZIP_MAX_FILES + 1 个小文件
        big = tmp_path / "big"
        big.mkdir()
        for i in range(_ZIP_MAX_FILES + 1):
            (big / f"f{i}.txt").write_text("x")
        ex = FileExecutor(workspace_root=str(tmp_path))
        with pytest.raises(ValueError, match="TOO_MANY_FILES"):
            _collect_zip_targets(ex, ["big"])

    def test_too_large_raises(self, tmp_path: Path, monkeypatch) -> None:
        # 用 monkeypatch 把上限改小，避免真创 2GB 文件
        monkeypatch.setattr("api.routes.file_download._ZIP_MAX_TOTAL_BYTES", 100)
        (tmp_path / "fat.bin").write_bytes(b"x" * 200)
        ex = FileExecutor(workspace_root=str(tmp_path))
        with pytest.raises(ValueError, match="TOO_LARGE"):
            _collect_zip_targets(ex, ["fat.bin"])


# ============================================================
# _collect_zip_targets — 安全
# ============================================================


class TestSafety:
    def test_path_traversal_reported(self, executor: FileExecutor) -> None:
        targets, errors = _collect_zip_targets(executor, ["../../../etc/passwd"])
        assert targets == []
        assert len(errors) == 1
        assert "越权" in errors[0] or "不合法" in errors[0]

    def test_hidden_files_excluded_from_folder_zip(self, executor: FileExecutor) -> None:
        """sidecar/隐藏文件 (. 开头) 在文件夹打包时被跳过,与 listdir/search 行为对齐。"""
        # 在子文件夹下放一个 hidden sidecar
        ws_root = Path(executor.workspace_root)
        (ws_root / "下载" / "子文件夹" / ".IMG_001.png.meta.json").write_text("{}", encoding="utf-8")
        targets, errors = _collect_zip_targets(executor, ["下载/子文件夹"])
        arcs = [arc for _, arc in targets]
        # 普通文件保留
        assert "子文件夹/数据.csv" in arcs
        assert "子文件夹/nested.txt" in arcs
        # 隐藏文件被过滤
        assert not any(".meta.json" in a for a in arcs)


# ============================================================
# 端到端：用 zipstream-ng 真打包 + zipfile 解包验证
# ============================================================


class TestZipEndToEnd:
    def test_zip_contains_chinese_filenames_with_utf8(
        self, executor: FileExecutor
    ) -> None:
        """中文文件名 + UTF-8 flag bit 验证"""
        from zipstream import ZIP_DEFLATED, ZipStream

        targets, _ = _collect_zip_targets(executor, ["下载/子文件夹"])
        zs = ZipStream(compress_type=ZIP_DEFLATED, compress_level=1)
        for abs_path, arc in targets:
            zs.add_path(str(abs_path), arcname=arc)

        buf = b""
        for chunk in zs:
            buf += chunk

        with zipfile.ZipFile(io.BytesIO(buf)) as zf:
            names = sorted(zf.namelist())
            assert "子文件夹/nested.txt" in names
            assert "子文件夹/数据.csv" in names
            # 验证 UTF-8 flag
            for info in zf.infolist():
                assert info.flag_bits & 0x800, f"{info.filename} 缺 UTF-8 flag"
            # 验证内容
            assert zf.read("子文件夹/nested.txt") == b"nested"
            assert zf.read("子文件夹/数据.csv") == "a,b\n1,2".encode("utf-8")

    def test_errors_txt_appended_when_some_paths_missing(
        self, executor: FileExecutor
    ) -> None:
        """混合存在+不存在路径时，错误清单作为 _errors.txt 入 ZIP"""
        from zipstream import ZIP_DEFLATED, ZipStream

        targets, errors = _collect_zip_targets(
            executor,
            ["下载/图片.png", "下载/不存在.png"],
        )
        zs = ZipStream(compress_type=ZIP_DEFLATED, compress_level=1)
        for abs_path, arc in targets:
            zs.add_path(str(abs_path), arcname=arc)
        if errors:
            zs.add(("\n".join(errors)).encode("utf-8"), arcname="_errors.txt")

        buf = b""
        for chunk in zs:
            buf += chunk

        with zipfile.ZipFile(io.BytesIO(buf)) as zf:
            names = zf.namelist()
            assert "图片.png" in names
            assert "_errors.txt" in names
            err_content = zf.read("_errors.txt").decode("utf-8")
            assert "不存在.png" in err_content


# ============================================================
# 端到端 HTTP：真实走 ASGI 层，验证中文文件名不再让 endpoint 崩溃
# ============================================================


class TestHttpEndpoint:
    """用 TestClient 起最小 FastAPI app + dependency_overrides，
    真实经过 starlette/httpx 的 latin-1 header 编码层。
    """

    def _build_app(self, workspace: Path):
        from fastapi import FastAPI

        from api.deps import OrgContext, get_org_context
        from api.routes.file_download import router

        app = FastAPI()
        app.include_router(router, prefix="/files")

        async def _fake_ctx() -> OrgContext:
            return OrgContext(user_id="u-test", org_id=None)

        app.dependency_overrides[get_org_context] = _fake_ctx
        return app

    def test_chinese_folder_download_returns_200(
        self, workspace: Path, monkeypatch
    ) -> None:
        """中文文件夹名（archive_name = 子文件夹.zip）不再让 uvicorn latin-1 编码崩溃"""
        from fastapi.testclient import TestClient

        # 让 get_executor 使用临时 workspace 而不是配置里的 NAS 路径
        from core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        app = self._build_app(workspace)
        client = TestClient(app)

        # 注：fixture workspace 里 user_id="u-test" 散客 → executor 会在
        # workspace/personal/{hash}/ 下找，我们的测试 fixture 文件在根
        # 用 list_endpoint 不可行 — 改成直接传根文件
        # 但 FileExecutor 散客模式会强制隔离到 personal/{hash}/，
        # 简化：构造一个散客隔离目录下的中文文件夹
        from services.file_executor import FileExecutor
        ex = FileExecutor(workspace_root=str(workspace), user_id="u-test")
        Path(ex.workspace_root).mkdir(parents=True, exist_ok=True)
        (Path(ex.workspace_root) / "中文夹").mkdir()
        (Path(ex.workspace_root) / "中文夹" / "x.txt").write_text("hi")

        resp = client.post(
            "/files/workspace/download_zip",
            json={"paths": ["中文夹"]},
        )
        assert resp.status_code == 200
        cd = resp.headers["content-disposition"]
        # filename= 部分必须 ASCII 安全（不含中文）
        # filename*=UTF-8'' 部分携带 percent-encoded 中文
        assert "filename=" in cd
        assert "filename*=UTF-8''" in cd
        # latin-1 自检（TestClient 已经走过，能拿到 resp 就说明没崩）
        cd.encode("latin-1")
