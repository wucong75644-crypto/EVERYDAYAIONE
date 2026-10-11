"""Public web, repository, feed, search and video adapters."""
import base64
import html
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urlsplit, urljoin, parse_qs

from .contracts import ReachError, ReachItem, ReachResult, youtube_id
from .network import public_address


async def read_web(request, http):
    await public_address(request.url)
    body = (await http.get('https://r.jina.ai/' + request.url)).decode('utf-8', errors='replace')
    if not body.strip() or 'Markdown Content:' not in body:
        raise ReachError('UPSTREAM_CHANGED', '网页阅读没有取得可解析正文')
    title = body.splitlines()[0].removeprefix('Title:').strip()
    content = body.split('Markdown Content:', 1)[1].strip()
    if not content:
        raise ReachError('PLATFORM_BLOCKED', '网页阅读未返回正文')
    return ReachResult('web', 'jina', [ReachItem(title, request.url, content)],
                       warnings=['仅取得文本正文；未验证网页中的图片、视频和登录后内容。'])


async def read_rss(request, http):
    data = await http.get(request.url)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ReachError('UPSTREAM_CHANGED', '订阅源包含不支持的XML声明')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ReachError('UPSTREAM_CHANGED', '目标不是可解析的RSS或Atom订阅源') from None
    tag = root.tag.rsplit('}', 1)[-1]
    if tag not in {'rss', 'feed', 'RDF'}:
        raise ReachError('UPSTREAM_CHANGED', '目标不是RSS或Atom订阅源')
    entries = [node for node in root.iter() if node.tag.rsplit('}', 1)[-1] in {'item', 'entry'}]
    items = []
    for entry in entries[:request.max_results]:
        values = {node.tag.rsplit('}', 1)[-1]: node for node in entry}
        def text(name):
            node = values.get(name)
            return ''.join(node.itertext()) if node is not None else ''
        link_node = values.get('link')
        link = (link_node.get('href', '') or text('link')) if link_node is not None else ''
        if not link:
            continue
        content = text('content') or text('encoded') or text('description') or text('summary')
        content = html.unescape(re.sub('<[^>]+>', ' ', content))
        items.append(ReachItem(text('title'), urljoin(request.url, link), content, 'snippet',
                               text('published') or text('pubDate') or text('updated')))
    return ReachResult('rss', 'rss-xml', items,
                       warnings=['返回订阅源提供的条目，未逐条读取文章全文。'] if items else [])


async def github(request, http):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'EVERYDAYAI-Agent-Reach',
               'X-GitHub-Api-Version': '2022-11-28'}
    async def get(path):
        return json.loads(await http.get('https://api.github.com/' + path, headers=headers))
    if request.action == 'search':
        data = await get('search/repositories?' + urlencode({'q': request.query,
                                                             'per_page': request.max_results}))
        items = [ReachItem(row['full_name'], row['html_url'], row.get('description') or '', 'metadata')
                 for row in data.get('items', []) if not row.get('private')]
        return ReachResult('github', 'github-api', items,
                           warnings=['仓库搜索仅返回项目简介，需读取仓库了解完整说明。'] if items else [])
    parts = urlsplit(request.url).path.strip('/').split('/')
    if len(parts) < 2 or any(not re.fullmatch(r'[\w.-]+', p) for p in parts[:2]):
        raise ReachError('INVALID_URL', '请指定GitHub仓库或Issue链接')
    repo = '/'.join(parts[:2])
    if len(parts) == 2:
        info = await get('repos/' + repo)
        if info.get('private'):
            raise ReachError('UNSUPPORTED_OPERATION', '当前仅支持公开仓库')
        readme = await get('repos/' + repo + '/readme')
        content = base64.b64decode(readme['content']).decode('utf-8', errors='replace')
        return ReachResult('github', 'github-api', [ReachItem(info['full_name'], info['html_url'], content)])
    if len(parts) == 4 and parts[2] in {'issues', 'pull'} and parts[3].isdigit():
        path = f'repos/{repo}/issues/{parts[3]}'
        if request.action == 'read_comments':
            rows = await get(path + '/comments?' + urlencode({'per_page': request.max_results}))
            return ReachResult('github', 'github-api', [ReachItem('Issue评论', row['html_url'],
                row.get('body') or '', 'comment', row.get('created_at', '')) for row in rows],
                warnings=['只返回本页评论，未承诺覆盖全部讨论。'])
        row = await get(path)
        return ReachResult('github', 'github-api', [ReachItem(row['title'], row['html_url'],
            row.get('body') or '', 'full_text', row.get('created_at', ''))])
    raise ReachError('UNSUPPORTED_OPERATION', '当前支持仓库README和Issue/PR正文或评论链接')


async def exa(request, http, api_key):
    if not api_key:
        raise ReachError('CONFIG_REQUIRED', 'Exa API未配置；普通网页查询可使用web_search')
    data = json.loads(await http.request('POST', 'https://api.exa.ai/search',
        headers={'x-api-key': api_key}, json={'query': request.query, 'numResults': request.max_results,
                                             'contents': {'text': {'maxCharacters': 6000}}}))
    rows = data.get('results')
    if not isinstance(rows, list):
        raise ReachError('UPSTREAM_CHANGED', 'Exa未返回搜索结果结构')
    return ReachResult('exa', 'exa-api', [ReachItem(row.get('title', ''), row['url'],
        row.get('text') or '', 'full_text' if row.get('text') else 'metadata',
        row.get('publishedDate', '')) for row in rows])


async def youtube(request, runner, http):
    target = request.url
    if request.action != 'search':
        video_id = youtube_id(target)
        target = 'https://www.youtube.com/watch?v=' + video_id
    if request.action == 'search':
        target = f'ytsearch{request.max_results}:{request.query}'
    args = ['--no-config', '--skip-download', '--no-playlist', '--dump-single-json',
            '--no-warnings', '--socket-timeout', '15', '--retries', '0',
            '--extractor-retries', '0']
    if (runner.bin_dir / 'node').is_file():
        args.extend(['--js-runtimes', 'node:' + str(runner.bin_dir / 'node')])
    elif request.action == 'transcript':
        raise ReachError('CONFIG_REQUIRED', '视频字幕需要在独立运行环境配置Node运行时')
    if request.action == 'search':
        args.append('--flat-playlist')
    payload = await runner.run('yt-dlp', [*args, '--', target])
    if request.action == 'search':
        return ReachResult('youtube', 'yt-dlp', [ReachItem(row.get('title', ''),
            'https://www.youtube.com/watch?v=' + row['id'], row.get('description') or '', 'metadata')
            for row in payload.get('entries', []) if re.fullmatch(r'[\w-]{11}', row.get('id', ''))],
            warnings=['仅返回视频元数据，未读取视频字幕。'])
    title, url = payload.get('title', ''), payload.get('webpage_url', request.url)
    if request.action == 'read':
        return ReachResult('youtube', 'yt-dlp', [ReachItem(title, url, payload.get('description') or '', 'metadata')],
                           warnings=['视频简介不能代替视频内容；总结视频需获取字幕。'])
    subtitles = payload.get('subtitles') or payload.get('automatic_captions') or {}
    language = next((key for key in ('zh-Hans', 'zh', 'zh-CN', 'en') if key in subtitles),
                    next(iter(subtitles), None))
    candidates = subtitles.get(language, [])
    selected = next((row for row in candidates if row.get('ext') == 'json3'), None)
    if not selected:
        raise ReachError('NO_TRANSCRIPT', '未找到可读取字幕，不能据视频简介总结视频内容')
    data = json.loads(await http.get(selected['url']))
    lines = [f'[{row.get("tStartMs", 0) / 1000:.1f}s] ' +
             ''.join(segment.get('utf8', '') for segment in row.get('segs', []))
             for row in data.get('events', []) if row.get('segs')]
    if not lines:
        raise ReachError('NO_TRANSCRIPT', '字幕没有返回可用文本')
    return ReachResult('youtube', 'yt-dlp', [ReachItem(title, url, '\n'.join(lines), 'transcript')],
                       warnings=[f'字幕语言：{language}；自动字幕可能有识别错误。'])


async def youtube_comments(request, http, api_key, connection=None):
    video_id = youtube_id(request.url)
    token = (connection or {}).get('secret', {}).get('access_token')
    if not token and not api_key:
        raise ReachError('CONFIG_REQUIRED', 'YouTube评论读取需要配置API key或获授权的频道连接')
    params = {'part':'snippet','videoId':video_id,'maxResults':request.max_results,'textFormat':'plainText'}
    headers = {'Authorization':'Bearer ' + token} if token else {'X-Goog-Api-Key': api_key}
    data = json.loads(await http.get('https://www.googleapis.com/youtube/v3/commentThreads?' + urlencode(params), headers=headers))
    if not isinstance(data.get('items'), list):
        raise ReachError('UPSTREAM_CHANGED', 'YouTube未返回可解析评论列表')
    items = []
    for row in data['items']:
        comment = row['snippet']['topLevelComment']
        info = comment['snippet']
        items.append(ReachItem('YouTube评论 · ' + info.get('authorDisplayName', ''),
            'https://www.youtube.com/watch?' + urlencode({'v':video_id,'lc':comment['id']}),
            info.get('textDisplay') or '', 'comment', info.get('publishedAt', '')))
    return ReachResult('youtube', 'youtube-api', items,
        warnings=['仅返回本页顶层评论，未覆盖全部回复。'] if items else [])
