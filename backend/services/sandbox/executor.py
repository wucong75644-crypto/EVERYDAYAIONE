"""
沙盒执行器 — 三引擎产物协议(Phase 2)

主进程负责: AST 验证 + 子进程生命周期管理 + 三引擎产物聚合。
子进程负责: chdir + exec 用户代码 + 返回结果(sandbox_worker.py)。

产物三引擎(任一捕获即送达,完全冗余):
  A. Jupyter mimebundle 协议(emit_auto_hooks.py):
     plt.show/fig.show/Chart.show/display() + _repr_*_ 自动出图
  B. Runtime 写盘 diff(_auto_emit_missed):
     扫 output_dir 新增/修改文件 → 按扩展名自动构造 emit_image/file payload
  C. LLM 显式 emit(emit_protocol.py):
     emit_chart/file/image/table 显式声明,提供 title/label

去重: LLM emit + auto diff 触发同一文件时,以 LLM emit 为准(保留 title/label)。
详见 docs/document/TECH_沙盒产物协议.md
"""

import json
import os
import re
import time as _time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from services.agent.agent_result import AgentResult
from services.sandbox.validators import validate_code


# 用于漏 emit 告警的常见产物扩展名
_PRODUCT_EXTS = frozenset({
    ".xlsx", ".xls", ".csv", ".tsv",
    ".png", ".jpg", ".jpeg", ".svg", ".pdf",
    ".docx", ".pptx",
})

# [EMIT] 协议正则: [EMIT]{"kind":..., ...}[/EMIT]
_EMIT_RE = re.compile(r"\[EMIT\](?P<payload>\{.+?\})\[/EMIT\]", re.DOTALL)


class SandboxExecutor:
    """通用 Python 代码沙盒执行器"""

    def __init__(
        self,
        timeout: float = 120.0,
        max_result_chars: int = 8000,
        output_dir: Optional[str] = None,
        staging_dir: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        kernel_manager=None,
        conversation_id: str = "",
        skills_dir: str = "",
        user_id: str = "",
        org_id: Optional[str] = None,
    ) -> None:
        self._timeout = timeout
        self._max_result_chars = max_result_chars
        self._output_dir = output_dir        # 沙盒输出目录("下载/")
        self._staging_dir = staging_dir      # staging 数据目录(中间产物)
        self._workspace_dir = workspace_dir  # 用户 workspace 目录
        self._skills_dir = skills_dir        # 文件处理技能目录(只读)
        self._kernel_manager = kernel_manager  # KernelManager(有状态模式)
        self._conversation_id = conversation_id
        self._user_id = user_id              # 给 emit_file/image 上传 OSS 用
        self._org_id = org_id                # 给 emit_file/image 上传 OSS 用

    async def execute(
        self, code: str, description: str = "",
    ) -> AgentResult:
        """执行 Python 代码并返回结构化结果。

        产物通过 emit_chart/file/image/table 协议返回。
        result.summary 含 [EMIT] marker,由 tool_loop_executor 解析填进
        AgentResult.emit_payloads(本函数不直接产生 emit_payloads)。
        """
        # 1. AST 安全验证(主进程,快速拦截)
        error = validate_code(code)
        if error:
            return AgentResult(
                summary=f"代码验证失败:\n{error}",
                status="error",
                error_message=error,
                metadata={"retryable": True},
            )

        logger.info(
            f"SandboxExecutor | desc={description} | "
            f"code_len={len(code)} | subprocess=spawn"
        )

        # 2. 快照 output_dir(执行后比对,漏 emit 时打 WARNING)
        snapshot_before = self._snapshot_output_dir()

        # 3. 执行代码
        raw_result = await self._execute_code(code)

        logger.info(
            f"SandboxExecutor result | desc={description} | "
            f"result_len={len(raw_result)} | result={raw_result[:200]}"
        )

        is_error = raw_result.startswith("❌")
        is_timeout = raw_result.startswith("⏱")

        if is_error:
            return AgentResult(
                summary=raw_result.lstrip("❌ "),
                status="error",
                error_message=raw_result,
                metadata={"retryable": True},
            )
        if is_timeout:
            return AgentResult(
                summary=raw_result.lstrip("⏱ "),
                status="timeout",
                error_message=raw_result,
            )

        # 4. Engine C: 解析 LLM 显式 [EMIT] marker → emit_payloads
        #    chart/table 直接收集;file/image 上传 OSS 拿 url+workspace_path 写回
        summary, payloads = await self._parse_emit(raw_result)

        # 5. Engine B: 写盘 diff 兜底 — 扫 output_dir 新增/修改文件,
        #    LLM 漏 emit 时自动构造 payload + OSS 上传 + 加入 emit_payloads
        #    (对齐 Anthropic Code Execution runtime hook)
        auto_payloads = await self._auto_emit_missed(snapshot_before, payloads)
        if auto_payloads:
            payloads.extend(auto_payloads)

        return AgentResult(
            summary=summary,
            status="success",
            emit_payloads=payloads,
        )

    async def _parse_emit(
        self, content: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """解析 [EMIT] marker → (替换占位的 content, emit_payloads list)。

        - chart/table: payload 直接收集
        - file/image:  上传 OSS,把 url + workspace_path 写回 payload
        """
        if not content or not _EMIT_RE.search(content):
            return content, []

        emits: list[dict[str, Any]] = []
        for m in _EMIT_RE.finditer(content):
            try:
                emits.append(json.loads(m.group("payload")))
            except json.JSONDecodeError as e:
                logger.warning(f"[EMIT] 解析失败 | err={e}")

        if not emits:
            return content, []

        logger.info(
            f"[EMIT] markers | conv={self._conversation_id[:8]} | "
            f"count={len(emits)} | kinds={[e.get('kind') for e in emits]}"
        )

        # file/image 上传 OSS,把 url + workspace_path 写回 payload
        await self._upload_payload_files(emits)

        # 替换 marker → 占位文本(给 LLM 看的,防止它重复 emit)
        def _placeholder(m):
            try:
                payload = json.loads(m.group("payload"))
                kind = payload.get("kind", "?")
                hints = {
                    "chart": f"📊 图表已生成: {payload.get('title', '')}（前端将自动渲染）",
                    "file": f"📎 文件已生成: {payload.get('label') or payload.get('name', '')}（下载卡片将自动展示）",
                    "image": f"🖼️ 图片已生成: {payload.get('name', '')}（前端将自动展示）",
                    "table": f"📋 表格已生成: {payload.get('title', '') or '(无标题)'}（前端将自动渲染）",
                }
                return hints.get(kind, f"[已 emit:{kind}]")
            except Exception:
                return ""

        new_content = _EMIT_RE.sub(_placeholder, content)
        return new_content, emits

    async def _execute_code(self, code: str) -> str:
        """Kernel 模式单一执行路径(无 subprocess 降级)。
        Kernel 崩溃 → 销毁 → 重建 → 重试一次 → 仍失败报错。
        """
        if not (self._kernel_manager and self._conversation_id):
            return self._format_error(
                "沙盒服务未就绪,请稍后重试", retryable=True,
            )

        for attempt in range(2):
            try:
                kernel_ok = await self._kernel_manager.get_or_create(
                    self._conversation_id,
                    self._workspace_dir or "",
                    self._staging_dir or "",
                    self._output_dir or "",
                    skills_dir=self._skills_dir,
                )
                if not kernel_ok:
                    return self._format_error(
                        "沙盒资源紧张,请稍后重试", retryable=True,
                    )

                status, result = await self._kernel_manager.execute(
                    self._conversation_id, code, self._timeout,
                )

                if status != "crashed":
                    return result

                if attempt == 0:
                    logger.warning("Kernel 崩溃,尝试重建 | conv={}",
                                   self._conversation_id[:8])
                    await self._kernel_manager.destroy(self._conversation_id)
                    continue
                return self._format_error(
                    "沙盒执行异常,请稍后重试", retryable=True,
                )

            except (KeyError, RuntimeError, OSError) as e:
                logger.warning("Kernel 执行失败 | error=%s", e)
                return self._format_error(
                    f"沙盒执行失败: {e}", retryable=True,
                )

        return self._format_error("沙盒不可用", retryable=True)

    @staticmethod
    def _format_error(msg: str, retryable: bool = True) -> str:
        return f"❌ {msg}"

    def _snapshot_output_dir(self) -> dict[str, tuple[float, int]]:
        """快照 output_dir 现有文件 (执行前调用,用于漏 emit 告警)。"""
        files: dict[str, tuple[float, int]] = {}
        if not self._output_dir:
            return files
        dp = Path(self._output_dir)
        if dp.exists():
            for f in dp.iterdir():
                if f.is_file():
                    st = f.stat()
                    files[f.name] = (st.st_mtime, st.st_size)
        return files

    async def _upload_payload_files(self, payloads: list[dict]) -> None:
        """共用 OSS 上传逻辑(Engine B 自动检测 + Engine C LLM emit 都调用)。

        对 kind in (file, image) 的 payload 调 upload_to_payload 拿 CDN url,
        把 url/mime_type/workspace_path 写回 payload(原地修改)。
        """
        from services.file_upload import upload_to_payload

        file_image = [p for p in payloads if p.get("kind") in ("file", "image")]
        for p in file_image:
            rel_path = p.get("path", "")
            name = p.get("name") or os.path.basename(rel_path)
            size = p.get("size", 0)
            if self._workspace_dir and rel_path:
                # rel_path 可能是绝对路径(自动 detect)或相对路径(LLM emit)
                if os.path.isabs(rel_path):
                    host_dir = os.path.dirname(rel_path)
                else:
                    host_dir = os.path.dirname(
                        os.path.join(self._workspace_dir, rel_path)
                    )
            else:
                host_dir = self._output_dir or ""

            if not (host_dir and os.path.exists(os.path.join(host_dir, name))):
                logger.warning(
                    f"[EMIT] {p.get('kind')} file 不存在 | path={rel_path}"
                )
                continue
            uploaded = await upload_to_payload(
                name, size, host_dir, self._user_id, self._org_id,
            )
            if uploaded:
                p["url"] = uploaded.get("url", "")
                p["mime_type"] = uploaded.get("mime_type", "")
                if "workspace_path" in uploaded:
                    p["workspace_path"] = uploaded["workspace_path"]
                if not p.get("size"):
                    p["size"] = uploaded.get("size", 0)

    async def _auto_emit_missed(
        self,
        snapshot_before: dict[str, tuple[float, int]],
        existing_payloads: list[dict],
    ) -> list[dict]:
        """Engine B: 写盘 diff 兜底 — output_dir 新增/修改文件自动 emit。

        扫 output_dir 比对 snapshot, 找出 LLM 没显式 emit 但写到下载目录的文件,
        按扩展名分类(image vs file), 自动上传 OSS, 返回 payload list 供合并。

        去重: 跳过 LLM 已显式 emit 的文件(以 LLM emit 为准,保留 title/label)。

        对齐 Anthropic Code Execution runtime hook — LLM 不调 emit 也能送达前端。
        """
        if not self._output_dir:
            return []
        dp = Path(self._output_dir)
        if not dp.exists():
            return []

        # 已 emit 过的文件名集合(LLM 显式 emit 的优先,不再重复)
        emitted_names = {
            os.path.basename(p.get("path", "")) or p.get("name", "")
            for p in existing_payloads
            if p.get("kind") in ("file", "image")
        }
        emitted_names.discard("")

        # 图片扩展名集合(其余视为通用文件)
        _IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".bmp"})

        new_payloads: list[dict] = []
        for f in dp.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in _PRODUCT_EXTS:
                continue
            if f.name in emitted_names:
                continue  # LLM 已显式 emit,跳过
            st = f.stat()
            old = snapshot_before.get(f.name)
            if old and old == (st.st_mtime, st.st_size):
                continue  # 未变化,旧文件不算产物

            # 按扩展名构造 payload
            if f.suffix.lower() in _IMAGE_EXTS:
                from services.sandbox.emit_protocol import build_image_payload
                try:
                    payload = build_image_payload(str(f), alt=f.name)
                except Exception as e:
                    logger.warning(f"[AUTO_EMIT] image payload 构造失败 | file={f.name} | err={e}")
                    continue
            else:
                from services.sandbox.emit_protocol import build_file_payload
                try:
                    payload = build_file_payload(str(f))
                except Exception as e:
                    logger.warning(f"[AUTO_EMIT] file payload 构造失败 | file={f.name} | err={e}")
                    continue
            payload["auto_detected"] = True
            new_payloads.append(payload)

        if not new_payloads:
            return []

        logger.info(
            f"[AUTO_EMIT] LLM 漏 emit 自动兜底 | "
            f"files={[p.get('name') for p in new_payloads]} | "
            f"conv={self._conversation_id[:8]}"
        )
        # OSS 上传 + 写回 url/mime_type/workspace_path
        await self._upload_payload_files(new_payloads)
        return new_payloads
