"""Idempotent owned video bytes; no temporary-URL success fallback."""
import hashlib
from uuid import UUID

from services.workspace_coordination import finish_file_io


def object_key(task):
    UUID(str(task['id']));UUID(str(task['user_id']))
    owner=hashlib.md5(str(task['user_id']).encode(),usedforsecurity=False).hexdigest()[:8]
    tenant='org/'+str(UUID(str(task['org_id']))) if task.get('org_id') else 'personal/'+owner
    return tenant+'/videos/generated/agent/'+owner+'_'+str(task['id'])+'.mp4'


def receipt(oss,task,key,meta):
    headers=meta.headers
    checksum=headers.get('x-oss-meta-content-sha256')
    if (headers.get('x-oss-meta-source-task')!=str(task['id']) or not isinstance(checksum,str)
        or len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum)
        or not meta.etag or meta.content_length<=0):raise ValueError('VIDEO_STORED_BYTES_UNCONFIRMED')
    return {'object_key':key,'url':oss.get_url(key),'content_sha256':checksum,'etag':meta.etag,
        'size':meta.content_length,'mime_type':'video/mp4','name':str(task['id'])+'.mp4'}


async def persist(oss,task,url):
    key=object_key(task)
    if await finish_file_io(oss.bucket.object_exists,key):
        return receipt(oss,task,key,await finish_file_io(oss.bucket.get_object_meta,key))
    content,content_type=await oss._downloader.download(url,task['user_id'],'video',oss.MAX_VIDEO_SIZE)
    if len(content)<12 or content[4:8]!=b'ftyp':raise ValueError('VIDEO_MP4_BYTES_INVALID')
    checksum=hashlib.sha256(content).hexdigest()
    await finish_file_io(oss.bucket.put_object,key,content,headers={'Content-Type':'video/mp4',
        'x-oss-forbid-overwrite':'true','x-oss-meta-source-task':str(task['id']),'x-oss-meta-content-sha256':checksum})
    result=receipt(oss,task,key,await finish_file_io(oss.bucket.get_object_meta,key))
    if result['content_sha256']!=checksum or result['size']!=len(content):raise ValueError('VIDEO_STORED_BYTES_CHANGED')
    return result


async def verify(oss,task,output):
    key=object_key(task)
    if output.get('object_key')!=key:raise ValueError('VIDEO_OUTPUT_SCOPE_DENIED')
    actual=receipt(oss,task,key,await finish_file_io(oss.bucket.get_object_meta,key))
    if any(actual[k]!=output.get(k) for k in actual):raise ValueError('VIDEO_STORED_BYTES_CHANGED')
    return actual
