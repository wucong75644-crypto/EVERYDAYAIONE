"""
文件路由聚合入口

按职责拆分到 4 个子模块，本文件仅做聚合，对外暴露统一 `router`：

- `file_upload.py`   — 上传（双写 OSS + workspace 子目录）
- `file_browse.py`   — 列表 / 搜索 / 预览
- `file_manage.py`   — 删除 / 新建文件夹 / 重命名 / 移动
- `file_download.py` — 批量 ZIP 下载

共享 schema 与工厂在 `file_common.py`。

main.py 注册不变：`app.include_router(file.router, prefix="/api")`
"""

from fastapi import APIRouter

from . import file_browse, file_download, file_manage, file_upload

router = APIRouter(prefix="/files", tags=["文件"])

router.include_router(file_upload.router)
router.include_router(file_browse.router)
router.include_router(file_manage.router)
router.include_router(file_download.router)
