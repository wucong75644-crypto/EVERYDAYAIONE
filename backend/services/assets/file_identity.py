"""依据文件内容建立稳定、安全的资产身份。"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote


_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "video/mp4": "mp4",
}
_CONTROL_CHARACTERS = re.compile(
    r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]"
)


@dataclass(frozen=True)
class AssetIdentity:
    """解密后文件内容对应的规范身份。"""

    provider_name: Optional[str]
    canonical_name: str
    detected_mime_type: str
    detection_source: str
    content_sha256: str
    size: int


def identify_file(
    data: bytes,
    *,
    stable_id: str,
    provider_name: Optional[str] = None,
    content_disposition: Optional[str] = None,
) -> AssetIdentity:
    """内容优先识别文件类型，并生成不依赖上游文件名的规范名称。"""
    header_name = _content_disposition_filename(content_disposition)
    supplied_name = _sanitize_filename(provider_name or header_name)
    mime_type, source = _detect_content(data)
    extension = _MIME_EXTENSIONS.get(mime_type, "bin")
    stem = Path(supplied_name).stem if supplied_name else f"企微文件_{stable_id[:12]}"
    canonical_name = f"{stem}.{extension}"
    return AssetIdentity(
        provider_name=supplied_name,
        canonical_name=canonical_name,
        detected_mime_type=mime_type,
        detection_source=source,
        content_sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


def _sanitize_filename(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", Path(value).name).strip(" .")
    if not cleaned:
        return None
    path = Path(cleaned)
    suffix = path.suffix[:16]
    stem_limit = max(1, 180 - len(suffix))
    return f"{path.stem[:stem_limit]}{suffix}"


def _content_disposition_filename(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", value, re.IGNORECASE)
    if encoded:
        return unquote(encoded.group(1).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*("([^"]+)"|([^;]+))', value, re.IGNORECASE)
    if not plain:
        return None
    return (plain.group(2) or plain.group(3)).strip()


def _detect_content(data: bytes) -> tuple[str, str]:
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    )
    for signature, mime_type in signatures:
        if data.startswith(signature):
            return mime_type, "magic"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "magic"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4", "magic"
    archive_type = _detect_office_archive(data)
    if archive_type:
        return archive_type, "container"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/vnd.ms-excel", "container"
    delimited_type = _detect_delimited_text(data)
    if delimited_type:
        return delimited_type, "content"
    return "application/octet-stream", "fallback"


def _detect_office_archive(data: bytes) -> Optional[str]:
    if not data.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if "xl/workbook.xml" in names:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if "word/document.xml" in names:
        return (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )
    return None


def _detect_delimited_text(data: bytes) -> Optional[str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    for delimiter, mime_type in (
        (",", "text/csv"),
        ("\t", "text/tab-separated-values"),
    ):
        rows = list(csv.reader(lines[:20], delimiter=delimiter))
        widths = {len(row) for row in rows}
        if len(widths) == 1 and next(iter(widths)) >= 2:
            return mime_type
    return None
