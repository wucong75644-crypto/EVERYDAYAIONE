"""Copy authorized, version-bound workspace media to an immutable upload snapshot."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import tempfile

from services.file_resources import FileTargetResolver, FileTargetError, file_version
from services.tools.resource_access import ResourceAccessError
from services.workspace_coordination import finish_file_io
from .contracts import ReachError

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 120 * 1024 * 1024


def copy_media(executor, media, directory):
    from PIL import Image
    resolver = FileTargetResolver(executor, action='read')
    items, total = [], 0
    for index, selected in enumerate(media):
        try:
            target = resolver.resolve(selected.resource_ref)
            before = target.validate()
            if selected.name != target.path.name:
                raise ReachError('RESOURCE_CHANGED', '素材名称与选定文件不一致，请重新选择')
            size = before[1]
            total += size
            if selected.role != 'video' and size > 20 * 1024 * 1024:
                raise ReachError('MEDIA_LIMIT', '图片单文件上限20MB')
            if size <= 0 or size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ReachError('MEDIA_LIMIT', '单文件上限100MB，单次素材总量上限120MB')
            path = Path(directory) / (str(index) + target.path.suffix.lower())
            digest = hashlib.sha256()
            with target.path.open('rb') as source, path.open('xb') as output:
                opened = os.fstat(source.fileno())
                if (opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns) != before:
                    raise ReachError('RESOURCE_CHANGED', '素材读取前已变化')
                copied = 0
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > size:
                        raise ReachError('RESOURCE_CHANGED', '素材读取期间大小发生变化')
                    digest.update(chunk)
                    output.write(chunk)
            path.chmod(0o600)
            if before != file_version(target.path):
                raise ReachError('RESOURCE_CHANGED', '素材在读取期间变化，请重新选择')
            if selected.role == 'video':
                with path.open('rb') as source:
                    signature = source.read(12)
                if path.suffix != '.mp4' or signature[4:8] != b'ftyp':
                    raise ReachError('INVALID_MEDIA', '首期视频上传需MP4文件')
                mime = 'video/mp4'
            else:
                with Image.open(path) as image:
                    if image.width * image.height > 25_000_000:
                        raise ReachError('MEDIA_LIMIT', '图片分辨率超过单次解析上限，请先调整尺寸')
                    image.verify()
                    mime = {'PNG':'image/png', 'JPEG':'image/jpeg', 'WEBP':'image/webp'}.get(image.format)
                if not mime:
                    raise ReachError('INVALID_MEDIA', '图片需PNG、JPEG或WebP格式')
            items.append({'path':str(path),'role':selected.role,'name':selected.name,
                          'size':size,'mime_type':mime,'sha256':digest.hexdigest()})
        except (FileTargetError, ResourceAccessError, PermissionError):
            raise ReachError('ACCESS_DENIED', '素材不在当前授权范围、已变化或签名无效，请重新选择') from None
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ReachError('INVALID_MEDIA', '素材无法读取或不是可解析的媒体文件') from None
    return items


@asynccontextmanager
async def media_snapshot(executor, media, staging_dir):
    if not media:
        yield []
        return
    root = Path(staging_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ReachError('CONFIG_REQUIRED', '媒体暂存目录必须由服务账号独占')
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=root) as directory:
        # finish_file_io waits for an in-flight copy on cancellation, before
        # cleanup removes its directory; a thread cannot outlive its snapshot.
        items = await finish_file_io(copy_media, executor, media, directory)
        yield items
