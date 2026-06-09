"""
解析浏览器 DevTools 复制的 cURL 字符串

用例：管理员在前端粘贴 cURL → 后端自动提取 cookie + companyid，
无需用户手动找字段。

支持 Chrome / Safari 风格的 cURL（多行 \\ 续行 + 单/双引号包裹的值）。

只解析我们关心的字段，不实现完整的 cURL 解析。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


@dataclass
class ParsedCurl:
    """从 cURL 提取的关键信息"""
    url: str
    method: str = "GET"
    # 完整 cookie 字符串（原样保留）
    cookie_full: str = ""
    # 解析后的 cookie dict
    cookies: dict[str, str] = field(default_factory=dict)
    # 关键身份 cookie（_censeid）
    censeid: str = ""
    # 关键 header
    companyid: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # 请求体（原样字符串）
    data_raw: str = ""


class CurlParseError(ValueError):
    """cURL 字符串无法解析（格式不对/缺关键字段）"""


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    """把 "a=1; b=2; c=3" 解析成 dict（保留原值不解码）。"""
    out: dict[str, str] = {}
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_curl(curl_text: str) -> ParsedCurl:
    """
    解析 cURL 字符串。

    支持的标志：
      -X <method>          请求方法
      -H 'key: value'      header
      -b 'cookie string'   cookie
      --cookie '...'       同 -b
      --data-raw '...'     请求体
      --data '...'         请求体
      -d '...'             请求体

    Args:
        curl_text: 浏览器复制的 cURL 字符串（含续行符 \\）

    Returns:
        ParsedCurl 对象，至少包含 url；如果有 cookie/header 也会解析

    Raises:
        CurlParseError: cURL 格式异常（缺 URL / 引号未闭合 等）
    """
    if not curl_text or not curl_text.strip():
        raise CurlParseError("cURL 文本为空")

    # 清理：去掉续行符 + 多余空白
    text = re.sub(r"\\\s*\n", " ", curl_text.strip())
    text = re.sub(r"\s+", " ", text)

    # shlex 处理引号内的特殊字符（含 $、空格、$o55 这种）
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as e:
        raise CurlParseError(f"shlex 解析失败（可能引号未闭合）: {e}") from e

    if not tokens or tokens[0].lower() != "curl":
        raise CurlParseError("不是 cURL 命令（应以 'curl' 开头）")

    result = ParsedCurl(url="")
    method_override: str | None = None
    data_parts: list[str] = []

    i = 1
    while i < len(tokens):
        tok = tokens[i]

        if tok in ("-X", "--request"):
            if i + 1 < len(tokens):
                method_override = tokens[i + 1].upper()
                i += 2
                continue

        if tok in ("-H", "--header"):
            if i + 1 < len(tokens):
                _parse_header_into(tokens[i + 1], result)
                i += 2
                continue

        if tok in ("-b", "--cookie"):
            if i + 1 < len(tokens):
                cookie_str = tokens[i + 1]
                result.cookie_full = cookie_str
                result.cookies = parse_cookie_string(cookie_str)
                result.censeid = result.cookies.get("_censeid", "")
                i += 2
                continue

        if tok in ("--data-raw", "--data", "-d", "--data-binary"):
            if i + 1 < len(tokens):
                data_parts.append(tokens[i + 1])
                i += 2
                continue

        # 通用 flag 跳过（不消费下一个 token）
        if tok in (
            "--compressed", "-k", "--insecure", "-i", "--include",
            "-v", "--verbose", "-s", "--silent", "-L", "--location",
        ):
            i += 1
            continue

        # 带值的通用 flag（消费 1 个）
        if tok in ("-A", "--user-agent", "-e", "--referer", "-o", "--output"):
            i += 2
            continue

        # 不是 flag → 视为 URL（一般是第一个非 flag 参数）
        if not tok.startswith("-") and not result.url:
            result.url = tok
            i += 1
            continue

        # 其它未识别 flag，跳过
        i += 1

    if not result.url:
        raise CurlParseError("找不到 URL")

    # 决定方法：有 -X 用 -X；没有但有 body → POST；都没有 → GET
    if method_override:
        result.method = method_override
    elif data_parts:
        result.method = "POST"
    else:
        result.method = "GET"

    # 拼接 body（多个 --data 会被拼接，但快麦只用一个 --data-raw）
    result.data_raw = "&".join(data_parts)

    return result


def _parse_header_into(header_str: str, result: ParsedCurl) -> None:
    """解析 'key: value' 写入 result.headers 和关键字段。"""
    if ":" not in header_str:
        return
    key, value = header_str.split(":", 1)
    key_lower = key.strip().lower()
    value = value.strip()

    # cookie 也可能通过 -H 'cookie: ...' 传
    if key_lower == "cookie":
        result.cookie_full = value
        result.cookies = parse_cookie_string(value)
        result.censeid = result.cookies.get("_censeid", "")
        return

    result.headers[key_lower] = value

    # 提取关键字段
    if key_lower == "companyid":
        try:
            result.companyid = int(value)
        except (ValueError, TypeError):
            pass


def detect_source(parsed: ParsedCurl) -> str | None:
    """
    根据 URL 判断数据源类型。

    Returns:
        'thinktank' / 'viperp' / None
    """
    url_lower = parsed.url.lower()
    if "/kmzk/" in url_lower or "think_tank" in url_lower:
        return "thinktank"
    if "/report/" in url_lower or "viperp" in url_lower:
        return "viperp"
    return None
