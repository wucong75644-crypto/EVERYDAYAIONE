"""统一文件内容识别测试。"""

import io
import zipfile

import pytest

from services.assets.file_identity import identify_file


def test_csv_without_provider_filename_gets_stable_name() -> None:
    data = "月份,销售额\n1月,120\n2月,180\n".encode()

    identity = identify_file(data, stable_id="file-message-123456")

    assert identity.canonical_name == "企微文件_file-message.csv"
    assert identity.detected_mime_type == "text/csv"
    assert identity.detection_source == "content"
    assert identity.size == len(data)
    assert len(identity.content_sha256) == 64


def test_content_type_wins_over_wrong_provider_extension() -> None:
    identity = identify_file(
        b"%PDF-1.7\ncontent",
        stable_id="msg",
        provider_name="季度报表.csv",
    )

    assert identity.provider_name == "季度报表.csv"
    assert identity.canonical_name == "季度报表.pdf"
    assert identity.detected_mime_type == "application/pdf"


def test_content_disposition_supplies_name_when_callback_does_not() -> None:
    identity = identify_file(
        b"a\tb\n1\t2\n",
        stable_id="msg",
        content_disposition=(
            "attachment; filename*=UTF-8''%E5%B7%A5%E4%BD%9C%E7%B0%BF.tsv"
        ),
    )

    assert identity.provider_name == "工作簿.tsv"
    assert identity.canonical_name == "工作簿.tsv"


@pytest.mark.parametrize(
    ("member", "mime_type", "extension"),
    [
        (
            "xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        ),
        (
            "word/document.xml",
            (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            "docx",
        ),
    ],
)
def test_office_container_detection(
    member: str,
    mime_type: str,
    extension: str,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, "content")

    identity = identify_file(
        buffer.getvalue(), stable_id="msg", provider_name="文档.bin"
    )

    assert identity.detected_mime_type == mime_type
    assert identity.canonical_name == f"文档.{extension}"


def test_unsafe_provider_name_is_sanitized() -> None:
    identity = identify_file(
        b"unknown",
        stable_id="msg",
        provider_name="../../\u202ehidden.exe",
    )

    assert identity.provider_name == "hidden.exe"
    assert identity.canonical_name == "hidden.bin"
