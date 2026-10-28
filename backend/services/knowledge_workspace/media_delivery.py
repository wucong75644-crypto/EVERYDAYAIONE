"""Bounded derived previews and verified, immutable partial reads; no ACL cache."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import io
import json
import os
import stat
import threading
import time
from . import files

_verified = OrderedDict()
_thumbnails = OrderedDict()
_lock = threading.RLock()
THUMB_BUDGET = 8 * 1024 * 1024
LEASE_SECONDS = 300


def lease(secret, ctx, oid, bid, revision):
    payload = json.dumps([ctx.user_id,ctx.org_id,str(oid),str(bid),revision,int(time.time())+LEASE_SECONDS],separators=(',',':')).encode()
    data = base64.urlsafe_b64encode(payload).rstrip(b'=').decode()
    signature = hmac.new(secret.encode(),('kb-media-v1:'+data).encode(),hashlib.sha256).hexdigest()
    return data+'.'+signature


def read_lease(secret, token, oid, bid, revision):
    try:
        data, signature = token.split('.')
        expected = hmac.new(secret.encode(),('kb-media-v1:'+data).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        user,org,o,b,r,expires = json.loads(base64.urlsafe_b64decode(data+'='*(-len(data)%4)))
        if (o,b,r)!=(str(oid),str(bid),revision) or expires<=time.time():raise ValueError()
        return user,org
    except (ValueError,TypeError,KeyError):
        files.fail('KNOWLEDGE_MEDIA_LEASE','播放授权已过期，请重新点击播放',401)


def verified_file(root, version):
    path=files.private_path(root,version['storage_org'],version['storage_owner'],version['version_id'])
    stream=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        stream=os.fdopen(fd,'rb')
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size!=version['size'] or not 0<info.st_size<=files.MAX_BYTES:
            stream.close();files.fail('KNOWLEDGE_FILE_INTEGRITY','文件校验失败',503)
        key=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,version['sha256'])
        with _lock:
            valid=key in _verified
        if not valid:
            digest=hashlib.sha256()
            for chunk in iter(lambda:stream.read(256*1024),b''):digest.update(chunk)
            end=os.fstat(stream.fileno())
            if digest.hexdigest()!=version['sha256'] or (end.st_mtime_ns,end.st_ctime_ns,end.st_size)!=(info.st_mtime_ns,info.st_ctime_ns,info.st_size):
                stream.close();files.fail('KNOWLEDGE_FILE_INTEGRITY','文件校验失败',503)
            with _lock:
                _verified[key]=True
                while len(_verified)>128:_verified.popitem(last=False)
        stream.seek(0)
        return stream
    except OSError:
        if stream is not None:stream.close()
        files.fail('KNOWLEDGE_FILE_UNAVAILABLE','文件暂时不可用',503)


def thumbnail(stream, version):
    from PIL import Image, ImageOps
    key=version['sha256']
    with _lock:
        if key in _thumbnails:
            _thumbnails.move_to_end(key);return _thumbnails[key]
        try:
            with Image.open(stream) as image:
                if image.width*image.height>16000000:files.fail('KNOWLEDGE_IMAGE_LIMIT','图片尺寸过大',422)
                image=ImageOps.exif_transpose(image)
                image.thumbnail((512,512))
                out=io.BytesIO();image.convert('RGBA').save(out,format='WEBP',quality=82)
            data=out.getvalue()
        except (OSError,ValueError):files.fail('KNOWLEDGE_IMAGE_INVALID','图片无法预览',422)
        _thumbnails[key]=data
        while len(_thumbnails)>32 or sum(map(len,_thumbnails.values()))>THUMB_BUDGET:_thumbnails.popitem(last=False)
        return data


def byte_range(header, size):
    if not header:return 0,size-1
    try:
        unit,value=header.split('=')
        if unit!='bytes' or ',' in value:raise ValueError()
        left,right=value.split('-')
        if not left:
            count=int(right)
            if count<=0:raise ValueError()
            return max(0,size-count),size-1
        start=int(left);end=min(size-1,int(right)) if right else size-1
        if start<0 or start>=size or end<start:raise ValueError()
        return start,end
    except (ValueError,TypeError):raise ValueError('invalid byte range')


def chunks(stream,start,end):
    try:
        stream.seek(start);remaining=end-start+1
        while remaining:
            data=stream.read(min(256*1024,remaining))
            if not data:break
            remaining-=len(data);yield data
    finally:stream.close()
