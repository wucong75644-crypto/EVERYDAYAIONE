# OSS + CDN 存储方案设计文档

> **版本**：v1.0 | **状态**：设计完成 | **最后更新**：2026-01-21

---

## 目录

- [一、方案概述](#一方案概述)
- [二、双方案架构设计](#二双方案架构设计)
- [三、OSS 存储规划](#三oss-存储规划)
- [四、后端代码框架](#四后端代码框架)
- [五、前端代码框架](#五前端代码框架)
- [六、CDN 配置指南](#六cdn-配置指南)
- [七、安全机制](#七安全机制)
- [八、生命周期管理](#八生命周期管理)
- [九、成本优化策略](#九成本优化策略)
- [十、环境配置](#十环境配置)
- [十一、API 接口设计](#十一api-接口设计)
- [十二、数据库表设计](#十二数据库表设计)

---

## 一、方案概述

### 1.1 背景与目标

本项目需要存储用户上传的图片、AI 生成的图片/视频等媒体文件。考虑到：
- **域名备案进度**：域名未备案前无法使用 CDN
- **成本控制**：OSS 直接下载流量费用高（147元/100GB），CDN 可节省约 90%
- **灵活切换**：需要支持两种方案无缝切换

### 1.2 成本对比

| 方案 | 下行流量价格 | 100GB 费用 | 节省比例 |
|------|-------------|-----------|---------|
| OSS 直接下载 | 0.50元/GB（外网） | ~50元 | - |
| CDN + OSS | 0.24元/GB（CDN） + 回源 | ~25元 | **~50%** |
| CDN + OSS（高缓存命中） | 主要是 CDN 费用 | ~15元 | **~70%** |

> 注：实际价格以阿里云官网为准，以上为估算值

### 1.3 方案选择

| 场景 | 使用方案 | 说明 |
|------|---------|------|
| 域名未备案 | **方案 A：OSS 直连** | 使用签名 URL 直接访问 OSS |
| 域名已备案 | **方案 B：CDN 加速** | CDN 缓存 + OSS 回源 |

---

## 二、双方案架构设计

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         文件存储架构                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  【上传流程】（两种方案相同）                                         │
│  ┌────────┐     ┌──────────┐     ┌─────────────┐                   │
│  │  前端  │ ──→ │ 后端API  │ ──→ │  阿里云STS  │                   │
│  │        │     │获取凭证   │     │  临时凭证   │                   │
│  └────────┘     └──────────┘     └─────────────┘                   │
│       │                                                             │
│       │ STS凭证                                                     │
│       ↓                                                             │
│  ┌────────┐                      ┌─────────────┐                   │
│  │  前端  │ ────────────────────→│  OSS Bucket │                   │
│  │ 直传   │      直接上传         │  (私有)     │                   │
│  └────────┘                      └─────────────┘                   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  【下载流程 - 方案A：OSS 直连】（域名未备案时使用）                    │
│  ┌────────┐     ┌──────────┐     ┌─────────────┐                   │
│  │  前端  │ ──→ │ 后端API  │ ──→ │   OSS SDK   │                   │
│  │        │     │获取URL   │     │  生成签名URL │                   │
│  └────────┘     └──────────┘     └─────────────┘                   │
│       │                                                             │
│       │ 签名URL（1小时有效）                                         │
│       ↓                                                             │
│  ┌────────┐                      ┌─────────────┐                   │
│  │  前端  │ ────────────────────→│  OSS Bucket │                   │
│  │ 访问   │      HTTPS直连        │             │                   │
│  └────────┘                      └─────────────┘                   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  【下载流程 - 方案B：CDN 加速】（域名备案后使用）                      │
│  ┌────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │  前端  │ ──→ │  CDN 节点   │ ──→ │  OSS Bucket │                │
│  │        │     │  (缓存)     │     │   (源站)    │                │
│  └────────┘     └─────────────┘     └─────────────┘                │
│                       ↑                                             │
│                   缓存命中                                           │
│                   直接返回                                           │
│                  (节省回源)                                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

1. **配置驱动切换**：通过环境变量一键切换两种方案
2. **代码零修改**：切换方案时只需改配置，业务代码无感知
3. **统一接口**：上传/下载接口保持一致
4. **安全优先**：私有 Bucket + STS 临时凭证 + 签名 URL

---

## 三、OSS 存储规划

### 3.1 Bucket 配置

| 配置项 | 值 | 说明 |
|-------|---|------|
| Bucket 名称 | `everydayai-media` | 根据实际命名 |
| 地域 | `oss-cn-hangzhou` | 选择离用户近的地域 |
| 存储类型 | 标准存储 | 频繁访问场景 |
| 读写权限 | **私有** | 安全考虑，必须私有 |
| 版本控制 | 关闭 | 节省成本 |
| 同城冗余 | 关闭 | MVP 阶段不需要 |

### 3.2 目录结构

```
everydayai-media/
├── avatars/                      # 用户头像（永久保存）
│   └── {user_id}/
│       └── {hash}.jpg
│
├── images/                       # 正式图片（永久保存，90天后转低频）
│   └── {user_id}/
│       └── {hash}_{timestamp}.{ext}
│
├── videos/                       # 正式视频（永久保存，90天后转低频）
│   └── {user_id}/
│       └── {hash}_{timestamp}.mp4
│
├── temp/                         # 临时文件（自动过期）
│   ├── edit-source/             # 编辑原图（7天过期）
│   │   └── {user_id}/{task_id}.{ext}
│   ├── edit-mask/               # 编辑蒙版（7天过期）
│   │   └── {user_id}/{task_id}.png
│   ├── upload-cache/            # 上传缓存（1天过期）
│   │   └── {user_id}/{uuid}.{ext}
│   └── batch-download/          # 批量下载ZIP（1小时过期）
│       └── {user_id}/{uuid}.zip
│
└── deleted/                      # 待删除文件（30天后物理删除）
    ├── images/
    └── videos/
```

### 3.3 文件命名规范

| 文件类型 | 命名规则 | 示例 |
|---------|---------|------|
| 用户头像 | `{sha256_hash}.jpg` | `a1b2c3d4.jpg` |
| 上传图片 | `{sha256_hash}_{timestamp}.{ext}` | `e5f6g7h8_1705824000.png` |
| 生成图片 | `{task_id}_{index}.{ext}` | `task123_0.jpg` |
| 生成视频 | `{task_id}.mp4` | `task456.mp4` |
| 编辑临时文件 | `{task_id}.{ext}` | `edit789.png` |

---

## 四、后端代码框架

### 4.1 目录结构

```
backend/
├── services/
│   └── storage/
│       ├── __init__.py
│       ├── config.py           # 存储配置
│       ├── oss_client.py       # OSS 客户端封装
│       ├── url_service.py      # URL 生成服务（核心切换逻辑）
│       ├── upload_service.py   # 上传服务
│       └── cleanup_service.py  # 清理服务
├── api/
│   └── storage/
│       ├── __init__.py
│       └── routes.py           # 存储相关 API
└── workers/
    └── cleanup_worker.py       # 清理任务 Worker
```

### 4.2 配置模块 (config.py)

```python
"""
存储服务配置
支持 OSS 直连 和 CDN 加速 两种模式
"""
from pydantic_settings import BaseSettings
from typing import Literal
from functools import lru_cache


class StorageConfig(BaseSettings):
    """存储配置"""

    # ==================== OSS 基础配置 ====================
    OSS_ACCESS_KEY_ID: str
    OSS_ACCESS_KEY_SECRET: str
    OSS_BUCKET_NAME: str = "everydayai-media"
    OSS_REGION: str = "oss-cn-hangzhou"
    OSS_ENDPOINT: str = "https://oss-cn-hangzhou.aliyuncs.com"
    OSS_INTERNAL_ENDPOINT: str = "https://oss-cn-hangzhou-internal.aliyuncs.com"

    # ==================== STS 配置 ====================
    STS_ROLE_ARN: str  # RAM 角色 ARN
    STS_DURATION_SECONDS: int = 900  # 临时凭证有效期（15分钟）

    # ==================== CDN 配置 ====================
    # 核心开关：是否启用 CDN（域名备案后设为 true）
    CDN_ENABLED: bool = False
    CDN_DOMAIN: str = ""  # CDN 加速域名，如：cdn.example.com
    CDN_PROTOCOL: Literal["http", "https"] = "https"

    # CDN 私有 Bucket 回源鉴权
    CDN_AUTH_KEY: str = ""  # CDN 鉴权密钥（可选，用于 URL 鉴权）
    CDN_AUTH_ENABLED: bool = False

    # ==================== URL 配置 ====================
    # OSS 签名 URL 有效期（秒）
    SIGNED_URL_EXPIRES: int = 3600  # 1小时
    # 批量下载 ZIP 有效期
    BATCH_DOWNLOAD_EXPIRES: int = 3600  # 1小时

    # ==================== 文件限制 ====================
    MAX_IMAGE_SIZE: int = 10 * 1024 * 1024  # 10MB
    MAX_AVATAR_SIZE: int = 5 * 1024 * 1024  # 5MB
    MAX_VIDEO_SIZE: int = 100 * 1024 * 1024  # 100MB
    ALLOWED_IMAGE_TYPES: list = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    ALLOWED_VIDEO_TYPES: list = ["video/mp4", "video/webm"]

    # ==================== 存储路径 ====================
    PATH_AVATARS: str = "avatars"
    PATH_IMAGES: str = "images"
    PATH_VIDEOS: str = "videos"
    PATH_TEMP: str = "temp"
    PATH_DELETED: str = "deleted"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_storage_config() -> StorageConfig:
    """获取存储配置（单例）"""
    return StorageConfig()
```

### 4.3 OSS 客户端封装 (oss_client.py)

```python
"""
阿里云 OSS 客户端封装
提供统一的 OSS 操作接口
"""
import oss2
from oss2 import Auth, Bucket, StsAuth
from oss2.credentials import EnvironmentVariableCredentialsProvider
from aliyunsdkcore import client as aliyun_client
from aliyunsdksts.request.v20150401.AssumeRoleRequest import AssumeRoleRequest
import json
import hashlib
from datetime import datetime
from typing import Optional, BinaryIO
from loguru import logger

from .config import get_storage_config


class OSSClient:
    """OSS 客户端"""

    def __init__(self):
        self.config = get_storage_config()
        self._bucket: Optional[Bucket] = None

    @property
    def bucket(self) -> Bucket:
        """获取 Bucket 实例（懒加载）"""
        if self._bucket is None:
            auth = Auth(
                self.config.OSS_ACCESS_KEY_ID,
                self.config.OSS_ACCESS_KEY_SECRET
            )
            # 服务器端使用内网 endpoint 节省流量
            self._bucket = Bucket(
                auth,
                self.config.OSS_INTERNAL_ENDPOINT,
                self.config.OSS_BUCKET_NAME
            )
        return self._bucket

    def get_sts_token(self, user_id: str) -> dict:
        """
        获取 STS 临时凭证（用于前端直传）

        Args:
            user_id: 用户ID，用于限制上传路径

        Returns:
            {
                "accessKeyId": "...",
                "accessKeySecret": "...",
                "securityToken": "...",
                "expiration": "2026-01-21T12:00:00Z",
                "bucket": "everydayai-media",
                "region": "oss-cn-hangzhou"
            }
        """
        # 创建 STS 客户端
        sts_client = aliyun_client.AcsClient(
            self.config.OSS_ACCESS_KEY_ID,
            self.config.OSS_ACCESS_KEY_SECRET,
            'cn-hangzhou'
        )

        # 构造 STS 请求
        request = AssumeRoleRequest()
        request.set_accept_format('json')
        request.set_RoleArn(self.config.STS_ROLE_ARN)
        request.set_RoleSessionName(f"user-{user_id}")
        request.set_DurationSeconds(self.config.STS_DURATION_SECONDS)

        # 限制上传路径（安全策略）
        policy = {
            "Version": "1",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["oss:PutObject"],
                    "Resource": [
                        f"acs:oss:*:*:{self.config.OSS_BUCKET_NAME}/images/{user_id}/*",
                        f"acs:oss:*:*:{self.config.OSS_BUCKET_NAME}/temp/*/{user_id}/*",
                        f"acs:oss:*:*:{self.config.OSS_BUCKET_NAME}/avatars/{user_id}/*",
                    ]
                }
            ]
        }
        request.set_Policy(json.dumps(policy))

        # 发起请求
        response = sts_client.do_action_with_exception(request)
        result = json.loads(response)
        credentials = result['Credentials']

        return {
            "accessKeyId": credentials['AccessKeyId'],
            "accessKeySecret": credentials['AccessKeySecret'],
            "securityToken": credentials['SecurityToken'],
            "expiration": credentials['Expiration'],
            "bucket": self.config.OSS_BUCKET_NAME,
            "region": self.config.OSS_REGION,
            "endpoint": self.config.OSS_ENDPOINT,
        }

    def upload_file(
        self,
        key: str,
        file_obj: BinaryIO,
        content_type: Optional[str] = None,
        headers: Optional[dict] = None
    ) -> str:
        """
        上传文件到 OSS（后端使用）

        Args:
            key: OSS 对象键（路径）
            file_obj: 文件对象
            content_type: 内容类型
            headers: 额外的 HTTP 头

        Returns:
            OSS 对象键
        """
        put_headers = headers or {}
        if content_type:
            put_headers['Content-Type'] = content_type

        self.bucket.put_object(key, file_obj, headers=put_headers)
        logger.info(f"文件上传成功: {key}")
        return key

    def delete_file(self, key: str) -> bool:
        """删除文件"""
        try:
            self.bucket.delete_object(key)
            logger.info(f"文件删除成功: {key}")
            return True
        except Exception as e:
            logger.error(f"文件删除失败: {key}, 错误: {e}")
            return False

    def move_file(self, source_key: str, dest_key: str) -> bool:
        """
        移动文件（复制后删除源文件）
        用于软删除时移动到 deleted/ 目录
        """
        try:
            self.bucket.copy_object(
                self.config.OSS_BUCKET_NAME,
                source_key,
                dest_key
            )
            self.bucket.delete_object(source_key)
            logger.info(f"文件移动成功: {source_key} -> {dest_key}")
            return True
        except Exception as e:
            logger.error(f"文件移动失败: {source_key}, 错误: {e}")
            return False

    def file_exists(self, key: str) -> bool:
        """检查文件是否存在"""
        return self.bucket.object_exists(key)

    def get_file_meta(self, key: str) -> Optional[dict]:
        """获取文件元信息"""
        try:
            meta = self.bucket.head_object(key)
            return {
                "content_type": meta.content_type,
                "content_length": meta.content_length,
                "last_modified": meta.last_modified,
                "etag": meta.etag,
            }
        except oss2.exceptions.NoSuchKey:
            return None

    def generate_signed_url(
        self,
        key: str,
        expires: Optional[int] = None,
        method: str = 'GET'
    ) -> str:
        """
        生成签名 URL（用于私有文件访问）

        Args:
            key: OSS 对象键
            expires: 有效期（秒），默认使用配置值
            method: HTTP 方法

        Returns:
            签名后的 URL
        """
        expires = expires or self.config.SIGNED_URL_EXPIRES

        # 使用外网 endpoint 生成 URL（给用户访问）
        auth = Auth(
            self.config.OSS_ACCESS_KEY_ID,
            self.config.OSS_ACCESS_KEY_SECRET
        )
        public_bucket = Bucket(
            auth,
            self.config.OSS_ENDPOINT,
            self.config.OSS_BUCKET_NAME
        )

        url = public_bucket.sign_url(method, key, expires)
        return url


# 单例
_oss_client: Optional[OSSClient] = None

def get_oss_client() -> OSSClient:
    """获取 OSS 客户端单例"""
    global _oss_client
    if _oss_client is None:
        _oss_client = OSSClient()
    return _oss_client
```

### 4.4 URL 生成服务 (url_service.py) ★核心

```python
"""
URL 生成服务（核心切换逻辑）

根据配置自动选择：
- 方案 A：OSS 签名 URL（域名未备案）
- 方案 B：CDN URL（域名已备案）
"""
import hashlib
import time
from typing import Optional
from urllib.parse import urljoin
from loguru import logger

from .config import get_storage_config
from .oss_client import get_oss_client


class URLService:
    """URL 生成服务"""

    def __init__(self):
        self.config = get_storage_config()
        self.oss_client = get_oss_client()

    @property
    def use_cdn(self) -> bool:
        """是否使用 CDN"""
        return self.config.CDN_ENABLED and bool(self.config.CDN_DOMAIN)

    def get_file_url(
        self,
        key: str,
        expires: Optional[int] = None,
        force_signed: bool = False
    ) -> str:
        """
        获取文件访问 URL（核心方法）

        根据配置自动选择 OSS 签名 URL 或 CDN URL

        Args:
            key: OSS 对象键（如 images/user123/abc.jpg）
            expires: 有效期（秒），仅 OSS 签名 URL 生效
            force_signed: 强制使用签名 URL（如敏感文件）

        Returns:
            文件访问 URL
        """
        # 临时文件或强制签名：始终使用 OSS 签名 URL
        if force_signed or key.startswith(self.config.PATH_TEMP):
            return self._get_oss_signed_url(key, expires)

        # 根据配置选择方案
        if self.use_cdn:
            return self._get_cdn_url(key)
        else:
            return self._get_oss_signed_url(key, expires)

    def _get_oss_signed_url(self, key: str, expires: Optional[int] = None) -> str:
        """
        方案 A：生成 OSS 签名 URL

        适用场景：
        - 域名未备案
        - 临时文件（batch-download 等）
        - 需要强制鉴权的文件
        """
        expires = expires or self.config.SIGNED_URL_EXPIRES
        url = self.oss_client.generate_signed_url(key, expires)
        logger.debug(f"生成 OSS 签名 URL: {key}, 有效期: {expires}秒")
        return url

    def _get_cdn_url(self, key: str) -> str:
        """
        方案 B：生成 CDN URL

        适用场景：
        - 域名已备案
        - 公开访问的媒体文件

        CDN 配置要求：
        - 开启私有 Bucket 回源
        - CDN 自动添加回源鉴权
        """
        base_url = f"{self.config.CDN_PROTOCOL}://{self.config.CDN_DOMAIN}"

        # 如果启用了 CDN URL 鉴权（可选）
        if self.config.CDN_AUTH_ENABLED and self.config.CDN_AUTH_KEY:
            url = self._sign_cdn_url(base_url, key)
        else:
            url = f"{base_url}/{key}"

        logger.debug(f"生成 CDN URL: {key}")
        return url

    def _sign_cdn_url(self, base_url: str, key: str) -> str:
        """
        生成带鉴权的 CDN URL（Type A 鉴权）

        格式：http://cdn.example.com/{key}?auth_key={timestamp}-{rand}-{uid}-{md5hash}

        参考：https://help.aliyun.com/zh/cdn/user-guide/type-a-signing
        """
        timestamp = int(time.time()) + self.config.SIGNED_URL_EXPIRES
        rand = "0"  # 可以使用随机数
        uid = "0"   # 用户标识，一般填 0

        # 计算签名
        path = f"/{key}"
        sign_str = f"{path}-{timestamp}-{rand}-{uid}-{self.config.CDN_AUTH_KEY}"
        md5hash = hashlib.md5(sign_str.encode()).hexdigest()

        auth_key = f"{timestamp}-{rand}-{uid}-{md5hash}"
        return f"{base_url}{path}?auth_key={auth_key}"

    def get_thumbnail_url(
        self,
        key: str,
        width: int = 200,
        height: int = 200,
        mode: str = "fill"
    ) -> str:
        """
        获取缩略图 URL（利用 OSS 图片处理）

        Args:
            key: 原图 OSS 键
            width: 缩略图宽度
            height: 缩略图高度
            mode: 缩放模式 (fill/fit/pad)

        Returns:
            缩略图 URL
        """
        # OSS 图片处理参数
        process = f"image/resize,m_{mode},w_{width},h_{height}/format,webp/quality,q_80"

        base_url = self.get_file_url(key)

        # 添加图片处理参数
        if "?" in base_url:
            return f"{base_url}&x-oss-process={process}"
        else:
            return f"{base_url}?x-oss-process={process}"

    def batch_get_urls(self, keys: list[str]) -> dict[str, str]:
        """
        批量获取文件 URL

        Args:
            keys: OSS 对象键列表

        Returns:
            {key: url} 映射
        """
        return {key: self.get_file_url(key) for key in keys}

    def get_upload_url(self, key: str, expires: int = 300) -> str:
        """
        获取预签名上传 URL（用于后端直传场景）

        Args:
            key: 目标 OSS 键
            expires: 有效期（秒）

        Returns:
            预签名上传 URL
        """
        return self.oss_client.generate_signed_url(key, expires, method='PUT')


# 单例
_url_service: Optional[URLService] = None

def get_url_service() -> URLService:
    """获取 URL 服务单例"""
    global _url_service
    if _url_service is None:
        _url_service = URLService()
    return _url_service
```

### 4.5 上传服务 (upload_service.py)

```python
"""
文件上传服务
处理文件验证、去重、上传等逻辑
"""
import hashlib
import io
import mimetypes
from datetime import datetime
from typing import Optional, Tuple, BinaryIO
from PIL import Image
from loguru import logger

from .config import get_storage_config
from .oss_client import get_oss_client
from .url_service import get_url_service


class UploadService:
    """上传服务"""

    def __init__(self):
        self.config = get_storage_config()
        self.oss_client = get_oss_client()
        self.url_service = get_url_service()

    async def process_image_upload(
        self,
        user_id: str,
        file_content: bytes,
        filename: str,
        content_type: str
    ) -> dict:
        """
        处理图片上传

        流程：
        1. 验证文件类型和大小
        2. 验证图片完整性
        3. 计算哈希进行去重
        4. 清理 EXIF 信息
        5. 上传到 OSS

        Returns:
            {
                "key": "images/user123/abc123_1705824000.jpg",
                "url": "https://...",
                "thumbnail_url": "https://...",
                "width": 1920,
                "height": 1080,
                "size": 102400,
                "hash": "abc123...",
                "is_duplicate": False
            }
        """
        # 1. 验证文件类型
        if content_type not in self.config.ALLOWED_IMAGE_TYPES:
            raise ValueError(f"不支持的图片类型: {content_type}")

        # 2. 验证文件大小
        if len(file_content) > self.config.MAX_IMAGE_SIZE:
            raise ValueError(f"图片大小超过限制: {self.config.MAX_IMAGE_SIZE // 1024 // 1024}MB")

        # 3. 验证图片完整性
        try:
            image = Image.open(io.BytesIO(file_content))
            image.verify()
            # 重新打开（verify 后需要重新打开）
            image = Image.open(io.BytesIO(file_content))
            width, height = image.size
        except Exception as e:
            raise ValueError(f"图片文件损坏或格式无效: {e}")

        # 4. 检查尺寸限制
        max_dimension = 4096
        if width > max_dimension or height > max_dimension:
            raise ValueError(f"图片尺寸超过限制: {max_dimension}x{max_dimension}")

        # 5. 计算文件哈希
        file_hash = hashlib.sha256(file_content).hexdigest()[:16]

        # 6. 检查是否重复（可选：查询数据库）
        # is_duplicate = await self._check_duplicate(user_id, file_hash)
        # if is_duplicate:
        #     return is_duplicate

        # 7. 清理 EXIF 并重新编码
        cleaned_content = self._clean_and_reencode_image(image, content_type)

        # 8. 生成文件名和路径
        ext = self._get_extension(content_type)
        timestamp = int(datetime.now().timestamp())
        key = f"{self.config.PATH_IMAGES}/{user_id}/{file_hash}_{timestamp}.{ext}"

        # 9. 上传到 OSS
        self.oss_client.upload_file(
            key=key,
            file_obj=io.BytesIO(cleaned_content),
            content_type=content_type
        )

        # 10. 生成访问 URL
        url = self.url_service.get_file_url(key)
        thumbnail_url = self.url_service.get_thumbnail_url(key, width=200, height=200)

        return {
            "key": key,
            "url": url,
            "thumbnail_url": thumbnail_url,
            "width": width,
            "height": height,
            "size": len(cleaned_content),
            "hash": file_hash,
            "is_duplicate": False
        }

    async def process_avatar_upload(
        self,
        user_id: str,
        file_content: bytes,
        content_type: str
    ) -> dict:
        """
        处理头像上传

        特殊处理：
        - 压缩到 400x400
        - 统一转为 JPEG
        - 质量 80%
        - 最大 200KB
        """
        # 验证
        if len(file_content) > self.config.MAX_AVATAR_SIZE:
            raise ValueError("头像原图大小超过 5MB 限制")

        # 打开并处理图片
        image = Image.open(io.BytesIO(file_content))

        # 转换为 RGB（处理 PNG 透明通道）
        if image.mode in ('RGBA', 'P'):
            background = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            background.paste(image, mask=image.split()[-1])
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')

        # 裁剪为正方形（居中裁剪）
        width, height = image.size
        size = min(width, height)
        left = (width - size) // 2
        top = (height - size) // 2
        image = image.crop((left, top, left + size, top + size))

        # 缩放到 400x400
        image = image.resize((400, 400), Image.Resampling.LANCZOS)

        # 压缩并保存
        output = io.BytesIO()
        quality = 80
        image.save(output, format='JPEG', quality=quality, optimize=True)

        # 确保不超过 200KB
        while output.tell() > 200 * 1024 and quality > 30:
            output = io.BytesIO()
            quality -= 10
            image.save(output, format='JPEG', quality=quality, optimize=True)

        compressed_content = output.getvalue()

        # 生成文件名
        file_hash = hashlib.sha256(compressed_content).hexdigest()[:16]
        key = f"{self.config.PATH_AVATARS}/{user_id}/{file_hash}.jpg"

        # 删除旧头像（如果存在）
        await self._delete_old_avatar(user_id)

        # 上传
        self.oss_client.upload_file(
            key=key,
            file_obj=io.BytesIO(compressed_content),
            content_type='image/jpeg'
        )

        url = self.url_service.get_file_url(key)

        return {
            "key": key,
            "url": url,
            "size": len(compressed_content)
        }

    def _clean_and_reencode_image(self, image: Image.Image, content_type: str) -> bytes:
        """清理 EXIF 并重新编码图片"""
        # 创建无 EXIF 的新图片
        data = list(image.getdata())
        clean_image = Image.new(image.mode, image.size)
        clean_image.putdata(data)

        # 保存
        output = io.BytesIO()
        format_map = {
            'image/jpeg': 'JPEG',
            'image/png': 'PNG',
            'image/gif': 'GIF',
            'image/webp': 'WEBP',
        }
        img_format = format_map.get(content_type, 'PNG')

        if img_format == 'JPEG':
            clean_image.save(output, format=img_format, quality=90, optimize=True)
        else:
            clean_image.save(output, format=img_format, optimize=True)

        return output.getvalue()

    def _get_extension(self, content_type: str) -> str:
        """根据 MIME 类型获取扩展名"""
        ext_map = {
            'image/jpeg': 'jpg',
            'image/png': 'png',
            'image/gif': 'gif',
            'image/webp': 'webp',
            'video/mp4': 'mp4',
            'video/webm': 'webm',
        }
        return ext_map.get(content_type, 'bin')

    async def _delete_old_avatar(self, user_id: str):
        """删除用户的旧头像"""
        # 列出用户头像目录下的所有文件
        prefix = f"{self.config.PATH_AVATARS}/{user_id}/"
        # 实际实现中需要列出并删除
        # 这里简化处理，实际项目中建议在数据库记录当前头像路径
        pass


# 单例
_upload_service: Optional[UploadService] = None

def get_upload_service() -> UploadService:
    """获取上传服务单例"""
    global _upload_service
    if _upload_service is None:
        _upload_service = UploadService()
    return _upload_service
```

### 4.6 清理服务 (cleanup_service.py)

```python
"""
文件清理服务
处理软删除、孤立文件清理等
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from loguru import logger

from .config import get_storage_config
from .oss_client import get_oss_client


class CleanupService:
    """清理服务"""

    def __init__(self):
        self.config = get_storage_config()
        self.oss_client = get_oss_client()

    async def soft_delete_file(self, key: str) -> bool:
        """
        软删除文件（移动到 deleted/ 目录）

        文件将在 30 天后由 OSS 生命周期规则自动删除
        """
        if not key:
            return False

        # 构造目标路径
        # images/user123/abc.jpg -> deleted/images/user123/abc.jpg
        dest_key = f"{self.config.PATH_DELETED}/{key}"

        success = self.oss_client.move_file(key, dest_key)

        if success:
            logger.info(f"文件软删除成功: {key} -> {dest_key}")

        return success

    async def restore_file(self, deleted_key: str, original_key: str) -> bool:
        """
        恢复软删除的文件

        Args:
            deleted_key: deleted/ 目录下的键
            original_key: 原始路径
        """
        return self.oss_client.move_file(deleted_key, original_key)

    async def batch_soft_delete(self, keys: List[str]) -> dict:
        """批量软删除"""
        results = {"success": [], "failed": []}

        for key in keys:
            if await self.soft_delete_file(key):
                results["success"].append(key)
            else:
                results["failed"].append(key)

        logger.info(f"批量软删除完成: 成功 {len(results['success'])}, 失败 {len(results['failed'])}")
        return results

    async def cleanup_orphan_files(self, referenced_keys: set) -> int:
        """
        清理孤立文件（数据库中无引用但 OSS 中存在）

        Args:
            referenced_keys: 数据库中引用的所有文件键集合

        Returns:
            清理的文件数量
        """
        cleaned_count = 0

        # 遍历需要检查的目录
        for prefix in [self.config.PATH_IMAGES, self.config.PATH_VIDEOS]:
            # 列出 OSS 中的所有文件
            # 实际实现中使用 oss2.ObjectIterator
            # 这里简化示意
            pass

        return cleaned_count


# 单例
_cleanup_service: Optional[CleanupService] = None

def get_cleanup_service() -> CleanupService:
    """获取清理服务单例"""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = CleanupService()
    return _cleanup_service
```

### 4.7 API 路由 (api/storage/routes.py)

```python
"""
存储相关 API 路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional

from backend.services.storage.oss_client import get_oss_client
from backend.services.storage.url_service import get_url_service
from backend.services.storage.upload_service import get_upload_service
# from backend.services.auth import get_current_user  # 认证依赖

router = APIRouter(prefix="/api/storage", tags=["storage"])


# ==================== 请求/响应模型 ====================

class STSTokenResponse(BaseModel):
    """STS 临时凭证响应"""
    accessKeyId: str
    accessKeySecret: str
    securityToken: str
    expiration: str
    bucket: str
    region: str
    endpoint: str


class FileURLRequest(BaseModel):
    """文件 URL 请求"""
    keys: List[str]


class FileURLResponse(BaseModel):
    """文件 URL 响应"""
    urls: dict  # {key: url}


class UploadResult(BaseModel):
    """上传结果"""
    key: str
    url: str
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size: int


# ==================== API 端点 ====================

@router.get("/upload-token", response_model=STSTokenResponse)
async def get_upload_token(
    # current_user = Depends(get_current_user)  # 需要登录
):
    """
    获取 OSS 上传临时凭证

    前端使用此凭证直传文件到 OSS
    """
    # user_id = current_user.id
    user_id = "test_user"  # 临时测试

    oss_client = get_oss_client()
    token = oss_client.get_sts_token(user_id)

    return STSTokenResponse(**token)


@router.post("/urls", response_model=FileURLResponse)
async def get_file_urls(
    request: FileURLRequest,
    # current_user = Depends(get_current_user)
):
    """
    批量获取文件访问 URL

    根据服务器配置自动返回 OSS 签名 URL 或 CDN URL
    """
    url_service = get_url_service()
    urls = url_service.batch_get_urls(request.keys)

    return FileURLResponse(urls=urls)


@router.post("/upload/image", response_model=UploadResult)
async def upload_image(
    file: UploadFile = File(...),
    # current_user = Depends(get_current_user)
):
    """
    上传图片（后端处理方式）

    适用于需要后端验证/处理的场景
    一般情况建议使用前端直传
    """
    # user_id = current_user.id
    user_id = "test_user"

    # 读取文件内容
    content = await file.read()

    upload_service = get_upload_service()
    result = await upload_service.process_image_upload(
        user_id=user_id,
        file_content=content,
        filename=file.filename,
        content_type=file.content_type
    )

    return UploadResult(**result)


@router.post("/upload/avatar", response_model=UploadResult)
async def upload_avatar(
    file: UploadFile = File(...),
    # current_user = Depends(get_current_user)
):
    """上传用户头像"""
    # user_id = current_user.id
    user_id = "test_user"

    content = await file.read()

    upload_service = get_upload_service()
    result = await upload_service.process_avatar_upload(
        user_id=user_id,
        file_content=content,
        content_type=file.content_type
    )

    return UploadResult(**result)


@router.get("/config")
async def get_storage_config_info():
    """
    获取存储配置信息（调试用）

    返回当前使用的存储方案
    """
    url_service = get_url_service()

    return {
        "mode": "cdn" if url_service.use_cdn else "oss_direct",
        "cdn_enabled": url_service.config.CDN_ENABLED,
        "cdn_domain": url_service.config.CDN_DOMAIN if url_service.use_cdn else None,
    }
```

---

## 五、前端代码框架

### 5.1 目录结构

```
frontend/src/
├── services/
│   └── storage/
│       ├── index.ts            # 导出
│       ├── config.ts           # 配置
│       ├── oss-upload.ts       # OSS 上传（前端直传）
│       └── file-utils.ts       # 文件工具函数
├── hooks/
│   └── useFileUpload.ts        # 上传 Hook
└── components/
    └── upload/
        ├── ImageUploader.tsx   # 图片上传组件
        └── AvatarUploader.tsx  # 头像上传组件
```

### 5.2 OSS 上传服务 (oss-upload.ts)

```typescript
/**
 * OSS 前端直传服务
 *
 * 使用 STS 临时凭证直接上传文件到 OSS
 * 避免文件经过后端服务器，节省带宽
 */
import OSS from 'ali-oss';

// ==================== 类型定义 ====================

interface STSToken {
  accessKeyId: string;
  accessKeySecret: string;
  securityToken: string;
  expiration: string;
  bucket: string;
  region: string;
  endpoint: string;
}

interface UploadOptions {
  /** 上传目录 */
  directory: 'images' | 'temp/edit-source' | 'temp/edit-mask' | 'avatars';
  /** 进度回调 */
  onProgress?: (percent: number) => void;
  /** 取消信号 */
  abortController?: AbortController;
}

interface UploadResult {
  /** OSS 对象键 */
  key: string;
  /** 文件名 */
  name: string;
  /** 文件大小 */
  size: number;
}

// ==================== STS 凭证管理 ====================

let cachedToken: STSToken | null = null;
let tokenExpireTime: number = 0;

/**
 * 获取 STS 临时凭证
 * 自动缓存，过期前 5 分钟刷新
 */
async function getSTSToken(): Promise<STSToken> {
  const now = Date.now();
  const bufferTime = 5 * 60 * 1000; // 5 分钟缓冲

  // 检查缓存是否有效
  if (cachedToken && tokenExpireTime - now > bufferTime) {
    return cachedToken;
  }

  // 请求新凭证
  const response = await fetch('/api/storage/upload-token', {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${localStorage.getItem('token')}`,
    },
  });

  if (!response.ok) {
    throw new Error('获取上传凭证失败');
  }

  const token: STSToken = await response.json();

  // 缓存凭证
  cachedToken = token;
  tokenExpireTime = new Date(token.expiration).getTime();

  return token;
}

/**
 * 创建 OSS 客户端
 */
async function createOSSClient(): Promise<OSS> {
  const token = await getSTSToken();

  return new OSS({
    region: token.region,
    accessKeyId: token.accessKeyId,
    accessKeySecret: token.accessKeySecret,
    stsToken: token.securityToken,
    bucket: token.bucket,
    endpoint: token.endpoint,
    secure: true,
  });
}

// ==================== 上传方法 ====================

/**
 * 上传文件到 OSS
 *
 * @example
 * ```ts
 * const result = await uploadFile(file, {
 *   directory: 'images',
 *   onProgress: (percent) => console.log(`上传进度: ${percent}%`),
 * });
 * console.log(result.key); // images/user123/abc_1705824000.jpg
 * ```
 */
export async function uploadFile(
  file: File,
  options: UploadOptions
): Promise<UploadResult> {
  const client = await createOSSClient();

  // 生成文件名
  const ext = file.name.split('.').pop()?.toLowerCase() || 'bin';
  const timestamp = Date.now();
  const randomStr = Math.random().toString(36).substring(2, 10);
  const fileName = `${randomStr}_${timestamp}.${ext}`;

  // 获取用户 ID（从 token 或其他地方）
  const userId = getUserId(); // 需要实现

  // 构造完整路径
  const key = `${options.directory}/${userId}/${fileName}`;

  // 上传配置
  const uploadOptions: OSS.PutObjectOptions = {
    headers: {
      'Content-Type': file.type,
      // 设置缓存控制（CDN 友好）
      'Cache-Control': 'max-age=31536000', // 1 年
    },
  };

  // 大文件使用分片上传
  if (file.size > 5 * 1024 * 1024) {
    return uploadMultipart(client, key, file, options);
  }

  // 小文件直接上传
  const result = await client.put(key, file, uploadOptions);

  // 上传完成
  options.onProgress?.(100);

  return {
    key,
    name: result.name,
    size: file.size,
  };
}

/**
 * 分片上传（大文件）
 */
async function uploadMultipart(
  client: OSS,
  key: string,
  file: File,
  options: UploadOptions
): Promise<UploadResult> {
  const result = await client.multipartUpload(key, file, {
    parallel: 4, // 并发数
    partSize: 1024 * 1024, // 1MB 每片
    progress: (percent: number) => {
      options.onProgress?.(Math.round(percent * 100));
    },
    headers: {
      'Content-Type': file.type,
      'Cache-Control': 'max-age=31536000',
    },
  });

  return {
    key,
    name: result.name,
    size: file.size,
  };
}

/**
 * 取消上传
 */
export function cancelUpload(client: OSS, uploadId: string) {
  // client.abortMultipartUpload(...)
}

// ==================== 辅助函数 ====================

function getUserId(): string {
  // 从 localStorage 或状态管理中获取
  // 这里需要根据实际认证方案实现
  const userStr = localStorage.getItem('user');
  if (userStr) {
    try {
      return JSON.parse(userStr).id;
    } catch {
      // ignore
    }
  }
  return 'anonymous';
}

/**
 * 验证文件类型
 */
export function validateFileType(file: File, allowedTypes: string[]): boolean {
  return allowedTypes.includes(file.type);
}

/**
 * 验证文件大小
 */
export function validateFileSize(file: File, maxSize: number): boolean {
  return file.size <= maxSize;
}

/**
 * 计算文件哈希（用于去重检查）
 */
export async function calculateFileHash(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('').substring(0, 16);
}
```

### 5.3 上传 Hook (useFileUpload.ts)

```typescript
/**
 * 文件上传 Hook
 *
 * 提供上传状态管理、进度跟踪、错误处理
 */
import { useState, useCallback } from 'react';
import { uploadFile, validateFileType, validateFileSize } from '@/services/storage/oss-upload';

// ==================== 类型定义 ====================

interface UploadState {
  /** 是否正在上传 */
  uploading: boolean;
  /** 上传进度 (0-100) */
  progress: number;
  /** 错误信息 */
  error: string | null;
  /** 上传结果 */
  result: UploadResult | null;
}

interface UploadResult {
  key: string;
  url: string;
  thumbnailUrl?: string;
}

interface UseFileUploadOptions {
  /** 允许的文件类型 */
  allowedTypes?: string[];
  /** 最大文件大小（字节） */
  maxSize?: number;
  /** 上传目录 */
  directory: 'images' | 'avatars' | 'temp/edit-source' | 'temp/edit-mask';
  /** 上传成功回调 */
  onSuccess?: (result: UploadResult) => void;
  /** 上传失败回调 */
  onError?: (error: Error) => void;
}

// ==================== Hook 实现 ====================

export function useFileUpload(options: UseFileUploadOptions) {
  const {
    allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
    maxSize = 10 * 1024 * 1024, // 10MB
    directory,
    onSuccess,
    onError,
  } = options;

  const [state, setState] = useState<UploadState>({
    uploading: false,
    progress: 0,
    error: null,
    result: null,
  });

  const upload = useCallback(async (file: File) => {
    // 重置状态
    setState({
      uploading: true,
      progress: 0,
      error: null,
      result: null,
    });

    try {
      // 验证文件类型
      if (!validateFileType(file, allowedTypes)) {
        throw new Error(`不支持的文件类型: ${file.type}`);
      }

      // 验证文件大小
      if (!validateFileSize(file, maxSize)) {
        throw new Error(`文件大小超过限制: ${(maxSize / 1024 / 1024).toFixed(1)}MB`);
      }

      // 上传文件
      const uploadResult = await uploadFile(file, {
        directory,
        onProgress: (percent) => {
          setState(prev => ({ ...prev, progress: percent }));
        },
      });

      // 获取访问 URL
      const urlResponse = await fetch('/api/storage/urls', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ keys: [uploadResult.key] }),
      });

      const { urls } = await urlResponse.json();

      const result: UploadResult = {
        key: uploadResult.key,
        url: urls[uploadResult.key],
      };

      setState({
        uploading: false,
        progress: 100,
        error: null,
        result,
      });

      onSuccess?.(result);
      return result;

    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '上传失败';

      setState({
        uploading: false,
        progress: 0,
        error: errorMessage,
        result: null,
      });

      onError?.(error instanceof Error ? error : new Error(errorMessage));
      throw error;
    }
  }, [allowedTypes, maxSize, directory, onSuccess, onError]);

  const reset = useCallback(() => {
    setState({
      uploading: false,
      progress: 0,
      error: null,
      result: null,
    });
  }, []);

  return {
    ...state,
    upload,
    reset,
  };
}
```

### 5.4 图片上传组件 (ImageUploader.tsx)

```tsx
/**
 * 图片上传组件
 *
 * 支持：
 * - 点击上传
 * - 拖拽上传
 * - 粘贴上传
 * - 进度显示
 * - 预览
 */
import React, { useCallback, useState } from 'react';
import { useFileUpload } from '@/hooks/useFileUpload';

interface ImageUploaderProps {
  /** 上传成功回调 */
  onUpload: (url: string, key: string) => void;
  /** 禁用状态 */
  disabled?: boolean;
  /** 自定义样式 */
  className?: string;
}

export function ImageUploader({ onUpload, disabled, className }: ImageUploaderProps) {
  const [preview, setPreview] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const { uploading, progress, error, upload, reset } = useFileUpload({
    directory: 'images',
    allowedTypes: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
    maxSize: 10 * 1024 * 1024,
    onSuccess: (result) => {
      onUpload(result.url, result.key);
    },
  });

  const handleFile = useCallback(async (file: File) => {
    // 显示本地预览
    const reader = new FileReader();
    reader.onload = (e) => setPreview(e.target?.result as string);
    reader.readAsDataURL(file);

    // 上传
    await upload(file);
  }, [upload]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) {
      handleFile(file);
    }
  }, [handleFile]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) {
          handleFile(file);
          break;
        }
      }
    }
  }, [handleFile]);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFile(file);
    }
    // 重置 input 以允许重复上传同一文件
    e.target.value = '';
  }, [handleFile]);

  return (
    <div
      className={`
        relative border-2 border-dashed rounded-lg p-6 text-center
        transition-colors cursor-pointer
        ${isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
        ${className}
      `}
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onPaste={handlePaste}
    >
      <input
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        onChange={handleInputChange}
        disabled={disabled || uploading}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
      />

      {preview ? (
        <div className="relative">
          <img
            src={preview}
            alt="预览"
            className="max-h-48 mx-auto rounded"
          />
          {uploading && (
            <div className="absolute inset-0 bg-black/50 flex items-center justify-center rounded">
              <div className="text-white">
                <div className="text-lg font-medium">{progress}%</div>
                <div className="w-32 h-2 bg-gray-600 rounded mt-2">
                  <div
                    className="h-full bg-blue-500 rounded transition-all"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="text-gray-500">
          <svg className="w-12 h-12 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          <p>点击、拖拽或粘贴上传图片</p>
          <p className="text-sm text-gray-400 mt-1">
            支持 JPG、PNG、GIF、WebP，最大 10MB
          </p>
        </div>
      )}

      {error && (
        <div className="mt-2 text-red-500 text-sm">{error}</div>
      )}
    </div>
  );
}
```

---

## 六、CDN 配置指南

### 6.1 配置步骤（域名备案后执行）

#### Step 1：添加 CDN 加速域名

1. 登录 [阿里云 CDN 控制台](https://cdn.console.aliyun.com/)
2. 点击「域名管理」→「添加域名」
3. 填写信息：
   - **加速域名**：`cdn.yourdomain.com`
   - **业务类型**：图片小文件
   - **源站信息**：
     - 类型：OSS 域名
     - 域名：`everydayai-media.oss-cn-hangzhou.aliyuncs.com`
   - **端口**：443

#### Step 2：配置 CNAME

1. 复制 CDN 分配的 CNAME 地址
2. 在域名 DNS 服务商处添加 CNAME 记录：
   ```
   主机记录：cdn
   记录类型：CNAME
   记录值：xxx.xxx.cdn20.com
   ```

#### Step 3：开启私有 Bucket 回源

> **重要**：OSS Bucket 设置为私有时必须开启

1. 在 CDN 控制台，选择域名 → 「回源配置」
2. 开启「私有 Bucket 回源」
3. 授权 CDN 访问私有 Bucket

#### Step 4：配置缓存规则

| 文件类型 | 缓存时间 | 说明 |
|---------|---------|------|
| 图片 (jpg/png/gif/webp) | 30 天 | 用户内容很少修改 |
| 视频 (mp4/webm) | 30 天 | 同上 |
| 其他 | 不缓存 | 动态内容 |

配置路径：域名管理 → 缓存配置 → 缓存过期时间

#### Step 5：配置 HTTPS

1. 域名管理 → HTTPS 配置
2. 上传 SSL 证书（或使用阿里云免费证书）
3. 开启「强制跳转 HTTPS」

#### Step 6：配置防盗链（可选）

1. 域名管理 → 访问控制 → Refer 防盗链
2. 设置白名单：`*.yourdomain.com`

### 6.2 启用 CDN 方案

完成以上配置后，修改环境变量：

```bash
# .env
CDN_ENABLED=true
CDN_DOMAIN=cdn.yourdomain.com
CDN_PROTOCOL=https
```

重启服务后，系统自动切换到 CDN 方案。

---

## 七、安全机制

### 7.1 安全架构

```
┌─────────────────────────────────────────────────────────────┐
│                        安全防护层                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  【凭证安全】                                                │
│  ┌─────────────────┐     ┌─────────────────┐               │
│  │   前端          │     │   后端          │               │
│  │                 │     │                 │               │
│  │ STS 临时凭证    │     │ AccessKey       │               │
│  │ (15分钟有效)    │     │ (仅后端持有)    │               │
│  │                 │     │                 │               │
│  │ 限制上传路径    │     │ 完全访问权限    │               │
│  └─────────────────┘     └─────────────────┘               │
│                                                             │
│  【访问控制】                                                │
│  ┌─────────────────────────────────────────┐               │
│  │  OSS Bucket: 私有读写                   │               │
│  │                                          │               │
│  │  上传: STS 凭证 + 路径限制              │               │
│  │  下载: 签名 URL (1小时有效) 或 CDN      │               │
│  └─────────────────────────────────────────┘               │
│                                                             │
│  【文件验证】                                                │
│  ┌─────────────────────────────────────────┐               │
│  │  1. MIME 类型验证（magic 库）           │               │
│  │  2. 文件大小限制                         │               │
│  │  3. 图片完整性验证（PIL）               │               │
│  │  4. 尺寸限制 (4096x4096)                │               │
│  │  5. EXIF 清理（隐私保护）               │               │
│  │  6. 文件重编码（防止恶意数据）          │               │
│  └─────────────────────────────────────────┘               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 STS 权限策略

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["oss:PutObject"],
      "Resource": [
        "acs:oss:*:*:everydayai-media/images/${user_id}/*",
        "acs:oss:*:*:everydayai-media/temp/*/${user_id}/*",
        "acs:oss:*:*:everydayai-media/avatars/${user_id}/*"
      ]
    }
  ]
}
```

**说明**：
- 仅允许 `PutObject`（上传），不允许删除、列举
- 路径限制到用户自己的目录
- 临时凭证 15 分钟过期

### 7.3 签名 URL 安全

- **有效期**：默认 1 小时
- **单次使用**：敏感操作可设置更短有效期
- **不可伪造**：使用 HMAC-SHA256 签名

---

## 八、生命周期管理

### 8.1 OSS 生命周期规则配置

在 OSS 控制台配置以下规则：

```xml
<!-- 规则 1：临时编辑文件 7 天后删除 -->
<Rule>
  <ID>cleanup-temp-edit</ID>
  <Prefix>temp/edit-</Prefix>
  <Status>Enabled</Status>
  <Expiration>
    <Days>7</Days>
  </Expiration>
</Rule>

<!-- 规则 2：上传缓存 1 天后删除 -->
<Rule>
  <ID>cleanup-upload-cache</ID>
  <Prefix>temp/upload-cache/</Prefix>
  <Status>Enabled</Status>
  <Expiration>
    <Days>1</Days>
  </Expiration>
</Rule>

<!-- 规则 3：批量下载 ZIP 1 天后删除 -->
<Rule>
  <ID>cleanup-batch-download</ID>
  <Prefix>temp/batch-download/</Prefix>
  <Status>Enabled</Status>
  <Expiration>
    <Days>1</Days>
  </Expiration>
</Rule>

<!-- 规则 4：待删除文件 30 天后物理删除 -->
<Rule>
  <ID>purge-deleted</ID>
  <Prefix>deleted/</Prefix>
  <Status>Enabled</Status>
  <Expiration>
    <Days>30</Days>
  </Expiration>
</Rule>

<!-- 规则 5：正式图片 90 天后转低频存储 -->
<Rule>
  <ID>archive-images</ID>
  <Prefix>images/</Prefix>
  <Status>Enabled</Status>
  <Transition>
    <Days>90</Days>
    <StorageClass>IA</StorageClass>
  </Transition>
</Rule>

<!-- 规则 6：正式视频 90 天后转低频存储 -->
<Rule>
  <ID>archive-videos</ID>
  <Prefix>videos/</Prefix>
  <Status>Enabled</Status>
  <Transition>
    <Days>90</Days>
    <StorageClass>IA</StorageClass>
  </Transition>
</Rule>
```

### 8.2 文件清理策略汇总

| 文件类型 | 存储目录 | 清理策略 |
|---------|---------|---------|
| 正式图片/视频 | `images/`, `videos/` | 用户删除 → `deleted/` → 30天后物理删除 |
| 用户头像 | `avatars/` | 更换时立即删除旧头像 |
| 编辑临时文件 | `temp/edit-*` | 7 天自动删除 |
| 上传缓存 | `temp/upload-cache/` | 1 天自动删除 |
| 批量下载 ZIP | `temp/batch-download/` | 1 天自动删除 |
| 孤立文件 | - | 每日定时任务检测移至 `deleted/` |

---

## 九、成本优化策略

### 9.1 存储成本优化

| 策略 | 节省比例 | 说明 |
|------|---------|------|
| 90天转低频存储 | ~40% | 旧文件自动转存 |
| 哈希去重 | ~10-20% | 相同文件不重复存储 |
| 图片压缩 | ~50% | WebP 格式 + 质量压缩 |
| 及时清理临时文件 | - | 避免无效存储 |

### 9.2 流量成本优化

| 策略 | 节省比例 | 说明 |
|------|---------|------|
| CDN 加速 | ~50-70% | CDN 流量费更便宜 |
| 缩略图 | ~80% | 列表页用小图 |
| 长缓存时间 | ~30% | 减少重复下载 |
| 内网传输 | 100% | 服务器使用内网 endpoint |

### 9.3 成本监控

建议配置阿里云费用预警：
- OSS 存储费用预警
- OSS 流量费用预警
- CDN 流量费用预警

---

## 十、环境配置

### 10.1 开发环境 (.env.development)

```bash
# OSS 配置
OSS_ACCESS_KEY_ID=your_dev_access_key_id
OSS_ACCESS_KEY_SECRET=your_dev_access_key_secret
OSS_BUCKET_NAME=everydayai-media-dev
OSS_REGION=oss-cn-hangzhou
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_INTERNAL_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com

# STS 配置
STS_ROLE_ARN=acs:ram::xxx:role/oss-upload-role

# CDN 配置（开发环境不启用）
CDN_ENABLED=false
CDN_DOMAIN=
```

### 10.2 生产环境 (.env.production)

```bash
# OSS 配置
OSS_ACCESS_KEY_ID=your_prod_access_key_id
OSS_ACCESS_KEY_SECRET=your_prod_access_key_secret
OSS_BUCKET_NAME=everydayai-media
OSS_REGION=oss-cn-hangzhou
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_INTERNAL_ENDPOINT=https://oss-cn-hangzhou-internal.aliyuncs.com

# STS 配置
STS_ROLE_ARN=acs:ram::xxx:role/oss-upload-role
STS_DURATION_SECONDS=900

# CDN 配置（域名备案后启用）
CDN_ENABLED=true
CDN_DOMAIN=cdn.yourdomain.com
CDN_PROTOCOL=https

# URL 配置
SIGNED_URL_EXPIRES=3600
BATCH_DOWNLOAD_EXPIRES=3600

# 文件限制
MAX_IMAGE_SIZE=10485760
MAX_AVATAR_SIZE=5242880
MAX_VIDEO_SIZE=104857600
```

### 10.3 方案切换清单

从「OSS 直连」切换到「CDN 加速」需要：

- [ ] 域名完成备案
- [ ] 在 CDN 控制台添加加速域名
- [ ] 配置 CNAME DNS 解析
- [ ] 开启私有 Bucket 回源
- [ ] 配置 HTTPS 证书
- [ ] 配置缓存规则
- [ ] 修改环境变量 `CDN_ENABLED=true`
- [ ] 修改环境变量 `CDN_DOMAIN=cdn.yourdomain.com`
- [ ] 重启服务
- [ ] 验证 CDN 访问正常

---

## 十一、API 接口设计

### 11.1 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/storage/upload-token` | GET | 获取 STS 上传凭证 |
| `/api/storage/urls` | POST | 批量获取文件访问 URL |
| `/api/storage/upload/image` | POST | 后端上传图片 |
| `/api/storage/upload/avatar` | POST | 上传头像 |
| `/api/storage/config` | GET | 获取存储配置信息 |

### 11.2 接口详情

#### 获取上传凭证

```
GET /api/storage/upload-token
Authorization: Bearer {token}

Response 200:
{
  "accessKeyId": "STS.xxx",
  "accessKeySecret": "xxx",
  "securityToken": "xxx",
  "expiration": "2026-01-21T12:00:00Z",
  "bucket": "everydayai-media",
  "region": "oss-cn-hangzhou",
  "endpoint": "https://oss-cn-hangzhou.aliyuncs.com"
}
```

#### 批量获取文件 URL

```
POST /api/storage/urls
Authorization: Bearer {token}
Content-Type: application/json

Request:
{
  "keys": [
    "images/user123/abc.jpg",
    "images/user123/def.png"
  ]
}

Response 200:
{
  "urls": {
    "images/user123/abc.jpg": "https://cdn.example.com/images/user123/abc.jpg",
    "images/user123/def.png": "https://cdn.example.com/images/user123/def.png"
  }
}
```

---

## 十二、数据库表设计

### 12.1 文件记录表

```sql
-- 文件上传记录表（用于去重和引用计数）
CREATE TABLE file_uploads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),

  -- 文件信息
  oss_key VARCHAR(500) NOT NULL UNIQUE,
  file_hash VARCHAR(64) NOT NULL,  -- SHA256 前 16 位
  file_name VARCHAR(255),
  content_type VARCHAR(100),
  file_size BIGINT NOT NULL,

  -- 图片/视频特有
  width INT,
  height INT,
  duration_seconds INT,  -- 视频时长

  -- 引用计数
  reference_count INT DEFAULT 1,

  -- 时间戳
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  deleted_at TIMESTAMP WITH TIME ZONE,

  -- 索引
  INDEX idx_user_id (user_id),
  INDEX idx_file_hash (user_id, file_hash),
  INDEX idx_deleted_at (deleted_at)
);

-- 文件删除日志表
CREATE TABLE file_deletion_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  original_key VARCHAR(500) NOT NULL,
  deleted_key VARCHAR(500) NOT NULL,  -- deleted/ 目录下的路径
  user_id UUID NOT NULL,

  deleted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

  INDEX idx_user_id (user_id),
  INDEX idx_deleted_at (deleted_at)
);
```

---

## 文档版本历史

| 版本 | 日期 | 修改内容 |
|------|------|---------|
| v1.0 | 2026-01-21 | 初始版本，完成双方案设计 |
