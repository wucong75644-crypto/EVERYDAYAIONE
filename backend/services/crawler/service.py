"""MediaCrawler 子进程封装服务

通过 CLI 参数启动 MediaCrawler 子进程，解析 JSON 输出，
返回格式化结果供 Agent 大脑消费。
"""

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from core.config import get_settings
from services.crawler.errors import (
    CrawlerLoginRequiredError,
    CrawlerNotInstalledError,
    CrawlerProcessError,
    CrawlerTimeoutError,
)
from services.crawler.models import (
    PLATFORM_NAMES,
    SUPPORTED_PLATFORMS,
    CrawlItem,
    CrawlResult,
)


class CrawlerService:
    """MediaCrawler 子进程封装"""

    def __init__(self) -> None:
        settings = get_settings()
        self._crawler_dir = Path(settings.crawler_dir).resolve()
        self._python_bin = self._crawler_dir / "venv" / "bin" / "python"
        self._timeout = settings.crawler_timeout
        self._max_notes = settings.crawler_max_notes
        self._headless = settings.crawler_headless
        self._login_type = settings.crawler_login_type

    def is_available(self) -> bool:
        """检查 MediaCrawler 是否已安装"""
        return self._python_bin.exists() and (self._crawler_dir / "main.py").exists()

    def get_cookie_for_platform(self, platform: str) -> str:
        """从 Settings 获取指定平台的 Cookie"""
        settings = get_settings()
        cookie_map: Dict[str, Optional[str]] = {
            "xhs": settings.crawler_cookies_xhs,
            "dy": settings.crawler_cookies_dy,
            "ks": settings.crawler_cookies_ks,
            "bili": settings.crawler_cookies_bili,
            "wb": settings.crawler_cookies_wb,
            "tieba": settings.crawler_cookies_tieba,
            "zhihu": settings.crawler_cookies_zhihu,
        }
        return cookie_map.get(platform, "") or ""

    async def execute(
        self,
        platform: str,
        keywords: List[str],
        max_notes: int = 10,
        crawl_type: str = "search",
    ) -> CrawlResult:
        """执行爬取任务

        Args:
            platform: 平台代码 (xhs/dy/ks/bili/wb/tieba/zhihu)
            keywords: 搜索关键词列表
            max_notes: 最大抓取条数
            crawl_type: 爬取类型 (search/detail)

        Returns:
            CrawlResult 包含爬取结果或错误信息
        """
        if not self.is_available():
            raise CrawlerNotInstalledError(
                f"MediaCrawler 未安装，路径：{self._crawler_dir}"
            )

        if platform not in SUPPORTED_PLATFORMS:
            return CrawlResult(
                platform=platform,
                error=f"不支持的平台：{platform}，支持：{', '.join(sorted(SUPPORTED_PLATFORMS))}",
            )

        max_notes = min(max_notes, self._max_notes)
        start_time = time.monotonic()
        output_dir = tempfile.mkdtemp(prefix="crawl_")

        try:
            cli_args = self._build_cli_args(
                platform=platform,
                keywords=keywords,
                max_notes=max_notes,
                crawl_type=crawl_type,
                output_dir=output_dir,
            )

            platform_name = PLATFORM_NAMES.get(platform, platform)
            kw_str = ",".join(keywords)
            logger.info(
                f"CrawlerService start | platform={platform_name} | "
                f"keywords={kw_str} | max={max_notes} | type={crawl_type}"
            )

            stdout, stderr = await self._run_subprocess(cli_args)
            elapsed = time.monotonic() - start_time

            items = self._parse_results(output_dir, platform)

            logger.info(
                f"CrawlerService done | platform={platform_name} | "
                f"items={len(items)} | elapsed={elapsed:.1f}s"
            )

            return CrawlResult(
                platform=platform,
                items=items,
                total_found=len(items),
                elapsed_seconds=round(elapsed, 1),
            )

        except CrawlerTimeoutError:
            elapsed = time.monotonic() - start_time
            return CrawlResult(
                platform=platform,
                elapsed_seconds=round(elapsed, 1),
                error=f"爬取超时（{self._timeout}秒），请缩小搜索范围或稍后重试",
            )
        except CrawlerLoginRequiredError as e:
            return CrawlResult(platform=platform, error=str(e))
        except CrawlerProcessError as e:
            logger.error(
                f"CrawlerService error | platform={platform} | "
                f"code={e.returncode} | stderr={e.stderr[:500]}"
            )
            return CrawlResult(platform=platform, error=f"爬虫进程异常：{e.stderr[:200]}")
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def _build_cli_args(
        self,
        platform: str,
        keywords: List[str],
        max_notes: int,
        crawl_type: str,
        output_dir: str,
    ) -> List[str]:
        """构建 MediaCrawler CLI 参数"""
        args = [
            str(self._python_bin),
            "main.py",
            "--platform", platform,
            "--type", crawl_type,
            "--keywords", ",".join(keywords),
            "--save_data_option", "json",
            "--save_data_path", output_dir,
            "--headless", str(self._headless).lower(),
            "--get_comment", "false",
            "--get_sub_comment", "false",
        ]

        # 登录方式
        cookie = self.get_cookie_for_platform(platform)
        if self._login_type == "cookie" and cookie:
            args.extend(["--lt", "cookie", "--cookies", cookie])
        elif self._login_type == "qrcode":
            args.extend(["--lt", "qrcode"])
        else:
            # cookie 模式但没配置 cookie，仍然尝试（依赖缓存的登录态）
            args.extend(["--lt", "cookie", "--cookies", ""])

        return args

    async def _run_subprocess(
        self, cli_args: List[str]
    ) -> tuple[str, str]:
        """启动子进程并等待完成"""
        process = await asyncio.create_subprocess_exec(
            *cli_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._crawler_dir),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise CrawlerTimeoutError(self._timeout)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            stderr_lower = stderr.lower()
            if "login" in stderr_lower or "登录" in stderr:
                platform_name = PLATFORM_NAMES.get("", "")
                raise CrawlerLoginRequiredError(platform_name)
            raise CrawlerProcessError(process.returncode, stderr)

        return stdout, stderr

    def _parse_results(self, output_dir: str, platform: str) -> List[CrawlItem]:
        """解析 JSON 输出文件"""
        items: List[CrawlItem] = []
        output_path = Path(output_dir)

        # MediaCrawler 输出路径: {save_data_path}/{platform}/json/search_contents_{date}.json
        json_dir = output_path / platform / "json"
        if not json_dir.exists():
            logger.warning(f"JSON output dir not found: {json_dir}")
            return items

        for json_file in json_dir.glob("*contents*.json"):
            try:
                raw_items = json.loads(json_file.read_text(encoding="utf-8"))
                if not isinstance(raw_items, list):
                    raw_items = [raw_items]
                for raw in raw_items:
                    item = self._normalize_item(raw, platform)
                    if item:
                        items.append(item)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to parse {json_file}: {e}")

        return items

    def _normalize_item(self, raw: Dict, platform: str) -> Optional[CrawlItem]:
        """将各平台原始数据标准化为 CrawlItem"""
        title = raw.get("title") or raw.get("desc", "")
        if not title:
            return None

        content = raw.get("desc", "") or raw.get("content", "")

        # 图片列表：多平台字段名不同
        images_str = raw.get("image_list", "")
        images = [u.strip() for u in images_str.split(",") if u.strip()] if images_str else []

        # 标签
        tags_str = raw.get("tag_list", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        # URL
        url = raw.get("note_url", "") or raw.get("url", "")

        return CrawlItem(
            platform=platform,
            title=title[:255],
            content=content[:2000],
            author=raw.get("nickname", ""),
            url=url,
            liked_count=_safe_int(raw.get("liked_count")),
            comment_count=_safe_int(raw.get("comment_count")),
            share_count=_safe_int(raw.get("share_count")),
            collected_count=_safe_int(raw.get("collected_count")),
            publish_time=str(raw.get("time", "")),
            images=images[:5],
            tags=tags[:10],
            source_keyword=raw.get("source_keyword", ""),
        )

    def format_for_brain(self, items: List[CrawlItem], max_chars: int = 4000) -> str:
        """格式化为大脑可读文本"""
        if not items:
            return "未找到相关内容"

        platform_name = PLATFORM_NAMES.get(items[0].platform, items[0].platform)
        lines = [f"从{platform_name}找到 {len(items)} 条结果：\n"]

        for i, item in enumerate(items, 1):
            entry = f"{i}. 【{item.title}】\n"
            if item.author:
                entry += f"   作者：{item.author}"
            stats = []
            if item.liked_count:
                stats.append(f"赞{item.liked_count}")
            if item.collected_count:
                stats.append(f"藏{item.collected_count}")
            if item.comment_count:
                stats.append(f"评{item.comment_count}")
            if stats:
                entry += f" | {'/'.join(stats)}"
            entry += "\n"
            if item.content and item.content != item.title:
                preview = item.content[:200]
                if len(item.content) > 200:
                    preview += "..."
                entry += f"   {preview}\n"
            if item.url:
                entry += f"   链接：{item.url}\n"

            # 字符数限制
            if len("\n".join(lines)) + len(entry) > max_chars:
                lines.append(f"\n...还有 {len(items) - i + 1} 条结果未显示")
                break
            lines.append(entry)

        return "\n".join(lines)


def _safe_int(value) -> int:
    """安全转换为整数"""
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0
