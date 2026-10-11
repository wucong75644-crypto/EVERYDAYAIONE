"""Fixed bridge for video comments absent from the pinned CLI command surface."""
import asyncio
import json
import sys


async def execute(payload):
    from bilibili_api import Credential, comment, video
    from bilibili_api.utils.network import request_settings
    from bili_cli.client import get_self_info
    request_settings.set_wbi_retry_times(1)
    credential = Credential(**{k: v for k, v in payload['secret'].items()
        if k in {'sessdata','bili_jct','buvid3','buvid4','dedeuserid'}})
    identity = await get_self_info(credential)
    if str(identity.get('mid', '')) != payload['account_id']:
        raise ValueError('ACCOUNT_MISMATCH')
    request = payload['request']
    if request['action'] == 'upload_video':
        from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage
        media = payload['media']
        footage = next(item for item in media if item['role'] == 'video')
        cover = next(item for item in media if item['role'] == 'cover')
        class SingleAttemptUploader(VideoUploader):
            async def _preupload(self, page):
                data = await super()._preupload(page)
                from urllib.parse import urlsplit
                endpoint = urlsplit('https:' + data['endpoint'])
                if not endpoint.hostname or not endpoint.hostname.endswith('.bilivideo.com') or endpoint.port not in {None,443}:
                    raise ValueError('UNTRUSTED_UPLOAD_ENDPOINT')
                data['threads'] = min(2, max(1, int(data['threads'])))
                return data
            async def _upload_chunk(self, *args):
                result = await super()._upload_chunk(*args)
                if not result['ok']:
                    raise ValueError('UPLOAD_INCOMPLETE')
                return result
        uploader = SingleAttemptUploader([VideoUploaderPage(footage['path'], request['title'], request['content'])],
            meta={'title':request['title'],'desc':request['content'],'tid':request['category_id'],
                  'tag':','.join(request['tags']),'copyright':1 if request['original'] else 2,
                  'source':request['source_url']},
            credential=credential, cover=cover['path'])
        response = await uploader.start()
        bvid = response.get('bvid', '')
        import re
        if not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid):
            raise ValueError('WRITE_UNCERTAIN')
        return {'receipt': {'action':'upload_video','remote_id':bvid,'account_id':payload['account_id'],
            'url':'https://www.bilibili.com/video/' + bvid,'stage':'submitted','visibility':'public'}}
    info = await video.Video(bvid=payload['bvid'], credential=credential).get_info()
    response = await comment.send_comment(payload['request']['content'], oid=int(info['aid']),
        type_=comment.CommentResourceType.VIDEO, credential=credential)
    remote_id = str(response.get('rpid_str') or response.get('rpid') or '')
    if not remote_id.isdigit():
        raise ValueError('WRITE_UNCERTAIN')
    return {'receipt': {'action':'create_comment','remote_id':remote_id,
        'target':payload['request']['url'],'account_id':payload['account_id']}}


if __name__ == '__main__':
    try:
        data = sys.stdin.buffer.read(64001)
        if len(data) > 64000:
            raise ValueError('INVALID_ARGUMENTS')
        print(json.dumps(asyncio.run(execute(json.loads(data)))))
    except Exception:
        print(json.dumps({'ok':False,'error':{'code':'write_uncertain'}}))
        sys.exit(1)
