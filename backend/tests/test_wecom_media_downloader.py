"""WecomMediaDownloader 单元测试 — AES 解密、下载、扩展名推断"""

import base64

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.wecom.media_downloader import WecomMediaDownloader


class TestAESDecrypt:
    """AES-256-CBC 解密"""

    def test_valid_decrypt(self):
        """合法密文 → 解密成功"""
        from Crypto.Cipher import AES

        # 构造合法密文：32 字节 key + PKCS#7 padding
        raw_key = b"\x01" * 32
        aeskey = base64.b64encode(raw_key).decode()
        iv = raw_key[:16]

        plaintext = b"hello wecom media test!"
        # PKCS#7 padding（块大小 32）
        pad_len = 32 - (len(plaintext) % 32)
        padded = plaintext + bytes([pad_len]) * pad_len

        cipher = AES.new(raw_key, AES.MODE_CBC, iv)
        ciphertext = cipher.encrypt(padded)

        result = WecomMediaDownloader._aes_decrypt(ciphertext, aeskey)
        assert result == plaintext

    def test_invalid_key_returns_none(self):
        """非法 key → 返回 None"""
        result = WecomMediaDownloader._aes_decrypt(b"data", "not-base64!")
        assert result is None

    def test_empty_data_returns_none(self):
        """空数据 → 返回 None"""
        raw_key = b"\x01" * 32
        aeskey = base64.b64encode(raw_key).decode()
        result = WecomMediaDownloader._aes_decrypt(b"", aeskey)
        assert result is None


class TestGuessExt:
    """文件扩展名推断"""

    def test_from_filename(self):
        assert WecomMediaDownloader._guess_ext("photo.png", "image") == "png"

    def test_from_filename_case(self):
        assert WecomMediaDownloader._guess_ext("doc.PDF", "file") == "pdf"

    def test_fallback_image(self):
        assert WecomMediaDownloader._guess_ext(None, "image") == "jpg"

    def test_fallback_video(self):
        assert WecomMediaDownloader._guess_ext(None, "video") == "mp4"

    def test_fallback_file(self):
        assert WecomMediaDownloader._guess_ext(None, "file") == "bin"

    def test_no_extension(self):
        assert WecomMediaDownloader._guess_ext("noext", "image") == "jpg"


class TestGuessContentType:
    """MIME 类型推断"""

    def test_jpg(self):
        assert WecomMediaDownloader._guess_content_type("jpg") == "image/jpeg"

    def test_png(self):
        assert WecomMediaDownloader._guess_content_type("png") == "image/png"

    def test_mp4(self):
        assert WecomMediaDownloader._guess_content_type("mp4") == "video/mp4"

    def test_pdf(self):
        assert WecomMediaDownloader._guess_content_type("pdf") == "application/pdf"

    def test_unknown(self):
        assert WecomMediaDownloader._guess_content_type("xyz") == "application/octet-stream"


class TestDownloadAndStore:
    """download_and_store 端到端"""

    @pytest.mark.asyncio
    async def test_success_without_aeskey(self):
        """无 aeskey → 直接上传"""
        downloader = WecomMediaDownloader()

        with patch.object(
            downloader, "_download", new=AsyncMock(return_value=b"imagedata")
        ), patch(
            "services.wecom.media_downloader.get_oss_service"
        ) as mock_oss_factory:
            mock_oss = MagicMock()
            mock_oss.upload_bytes.return_value = {"url": "https://oss.example.com/img.jpg"}
            mock_oss_factory.return_value = mock_oss

            result = await downloader.download_and_store(
                url="https://wecom.example.com/img.jpg",
                user_id="u1",
                media_type="image",
            )

        assert result == "https://oss.example.com/img.jpg"

    @pytest.mark.asyncio
    async def test_download_failure(self):
        """下载失败 → 返回 None"""
        downloader = WecomMediaDownloader()

        with patch.object(
            downloader, "_download", new=AsyncMock(return_value=None)
        ):
            result = await downloader.download_and_store(
                url="https://wecom.example.com/fail.jpg",
                user_id="u1",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_aes_decrypt_failure(self):
        """AES 解密失败 → 返回 None"""
        downloader = WecomMediaDownloader()

        with patch.object(
            downloader, "_download", new=AsyncMock(return_value=b"baddata")
        ):
            result = await downloader.download_and_store(
                url="https://wecom.example.com/img.jpg",
                user_id="u1",
                aeskey="bad-key",
            )

        assert result is None
