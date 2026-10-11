"""Official single-session video upload with a verified channel and pinned host."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ReachError, ReachResult, validate_url


async def youtube_upload(request, connection, http, media):
    if not media or len(media) != 1 or media[0]['role'] != 'video':
        raise ReachError('MEDIA_REQUIRED', 'YouTube上传需选择一个视频文件')
    token = connection['secret'].get('access_token')
    if not token:
        raise ReachError('AUTH_REQUIRED', 'YouTube上传需要获授权的频道OAuth Token')
    headers = {'Authorization':'Bearer ' + token}
    identity = json.loads(await http.get('https://www.googleapis.com/youtube/v3/channels?part=id&mine=true', headers=headers))
    if connection['account_id'] not in [row['id'] for row in identity.get('items', [])]:
        raise ReachError('ACCOUNT_MISMATCH', '授权频道与组织连接不一致')
    item = media[0]
    _, response_headers = await http.request('POST',
        'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',
        headers={**headers,'X-Upload-Content-Length':str(item['size']),'X-Upload-Content-Type':item['mime_type']},
        json={'snippet':{'title':request.title,'description':request.content,'tags':request.tags,
            'categoryId':str(request.category_id)},'status':{'privacyStatus':request.visibility}}, include_headers=True)
    location = response_headers.get('location', '')
    try:
        parsed = urlsplit(validate_url(location))
    except ReachError:
        raise ReachError('WRITE_UNCERTAIN', '上传服务没有返回可核验会话，请勿重新投稿') from None
    if parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com' or not parsed.path.startswith('/upload/youtube/'):
        raise ReachError('WRITE_UNCERTAIN', '上传会话指向非允许地址，已停止发送凭据')
    async def stream():
        with Path(item['path']).open('rb') as source:
            while chunk := source.read(65536):
                yield chunk
    data = json.loads(await http.request('PUT', location, content=stream(),
        headers={**headers,'Content-Length':str(item['size']),'Content-Type':item['mime_type']}))
    video_id = data.get('id', '')
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ReachError('WRITE_UNCERTAIN', '上传没有返回视频ID，请先核对频道')
    # API projects without a successful audit may force uploaded videos private.
    actual_visibility = data.get('status', {}).get('privacyStatus', 'unknown')
    return ReachResult('youtube', 'youtube-api', status='partial',
        receipt={'action':'upload_video','remote_id':video_id,'account_id':connection['account_id'],
            'url':'https://www.youtube.com/watch?v=' + video_id,'stage':'uploaded',
            'requested_visibility':request.visibility,'visibility':actual_visibility},
        warnings=['视频已上传，平台处理与最终公开状态尚未核验；实际可见范围以回执和频道页面为准。'])
