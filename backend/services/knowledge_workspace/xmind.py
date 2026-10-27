"""Bounded, offline Xmind reader. Archive member names never become disk paths."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import re
import stat
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile
import zlib

from core.exceptions import AppException
from .contracts import CanvasLink
from .files import MAX_BYTES, clean_name, fail

MAX_EXPANDED = 50 * 1024 * 1024
MAX_CONTENT = 8 * 1024 * 1024
MAX_TOPICS = 1000
MAX_DEPTH = 64


@dataclass
class ImportPlan:
    title: str
    topics: list[dict]
    resources: dict[str, bytes]
    warnings: list[str]
    sha256: str

    def report(self):
        return {'topics': len(self.topics), 'images': sum(bool(t.get('image')) for t in self.topics),
                'links': sum(bool(t.get('link')) for t in self.topics),
                'summaries': sum('summary' in t for t in self.topics),
                'warnings': self.warnings}


def invalid(message='Xmind 内容无效或包含不支持的结构，未创建项目'):
    fail('KNOWLEDGE_INVALID_XMIND', message)


def member_name(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        invalid('压缩包包含不安全路径')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or str(path) != value.rstrip('/'):
        invalid('压缩包包含不安全路径')
    return value


def _xml(data):
    # Reject DTDs/entities including UTF-16 variants before using the stdlib parser.
    if b'<!DOCTYPE' in data.replace(b'\x00', b'').upper() or b'<!ENTITY' in data.replace(b'\x00', b'').upper():
        invalid('不支持含 DTD 或实体的 XML')
    root = ET.fromstring(data)
    def name(e): return e.tag.rsplit('}', 1)[-1]
    def direct(e, key): return next((c for c in e if name(c) == key), None)
    def text(e): return ''.join(e.itertext()) if e is not None else ''
    count = 0
    def topic(e, depth=0):
        nonlocal count
        count += 1
        if count > MAX_TOPICS: invalid('主题数量超过 1000 个')
        if depth > MAX_DEPTH: invalid('主题层级超过 64 层')
        t = {'id': e.get('id'), 'title': text(direct(e, 'title'))}
        href = next((v for k, v in e.attrib.items() if k.rsplit('}', 1)[-1] == 'href'), None)
        if href: t['href'] = href
        if e.get('branch'): t['branch'] = e.get('branch')
        image = direct(e, 'image')
        if image is not None: t['image'] = {'src': next((v for k,v in image.attrib.items() if k.rsplit('}',1)[-1]=='src'), '')}
        notes = direct(e, 'notes')
        if notes is not None: t['notes'] = {'plain': {'content': text(notes)}}
        children = direct(e, 'children')
        if children is not None:
            t['children'] = {}
            for g in children:
                if name(g) != 'topics': invalid('XML 包含不支持的主题分组')
                kind = g.get('type','attached')
                if kind in t['children']: invalid('XML 包含重复主题分组')
                t['children'][kind] = [topic(c,depth+1) for c in g if name(c)=='topic']
        summaries = direct(e, 'summaries')
        if summaries is not None:
            t['summaries'] = [{'range': s.get('range'), 'topicId': s.get('topic-id')} for s in summaries]
        for key in ('boundaries', 'relationships', 'marker-refs', 'labels'):
            value = direct(e,key)
            if value is not None: t[key] = text(value) or True
        return t
    sheets = []
    for e in root:
        if name(e) == 'sheet':
            rt = direct(e, 'topic')
            if rt is None: invalid()
            sheets.append({'title': text(direct(e,'title')), 'rootTopic': topic(rt)})
    return sheets


def parse(data: bytes) -> ImportPlan:
    if not data or len(data) > MAX_BYTES:
        fail('KNOWLEDGE_FILE_SIZE', 'Xmind 文件不能为空且不能超过 20MB', 413)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos)>2000 or sum(i.file_size for i in infos)>MAX_EXPANDED:
                invalid('压缩内容超过 50MB 或 2000 个条目')
            names = set()
            for info in infos:
                member_name(info.filename)
                mode = info.external_attr >> 16
                if info.filename in names or info.flag_bits & 1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    invalid('压缩包含重复、加密或特殊条目')
                names.add(info.filename)
                if info.file_size > MAX_BYTES and info.filename not in ('content.json','content.xml'):
                    invalid('单个资源超过 20MB')
            content = 'content.json' if 'content.json' in names else 'content.xml'
            if content not in names or archive.getinfo(content).file_size > MAX_CONTENT: invalid('缺少导图内容或内容超过 8MB')
            raw = archive.read(content)
            def unique_object(pairs):
                result = {}
                for key,value in pairs:
                    if key in result: invalid('导图 JSON 包含重复字段')
                    result[key] = value
                return result
            sheets = json.loads(raw,object_pairs_hook=unique_object) if content.endswith('.json') else _xml(raw)
            if not isinstance(sheets,list) or not 1<=len(sheets)<=50: invalid()
            topics, resources, warnings, seen = [], {}, [], set()
            total_text = 0
            def warn(message):
                if message not in warnings: warnings.append(message)
            def resource(src):
                if not isinstance(src,str): invalid()
                path = unquote(src.removeprefix('xap:')).lstrip('/') if src.startswith('xap:') else unquote(src)
                member_name(path)
                if path not in names or archive.getinfo(path).is_dir(): invalid('引用的内嵌资源缺失，未创建项目')
                try: clean_name(PurePosixPath(path).name)
                except AppException: invalid('内嵌资源格式暂不支持，未创建项目')
                if path not in resources: resources[path] = archive.read(path)
                return path
            def visit(t, parent, order, depth):
                nonlocal total_text
                if not isinstance(t,dict) or depth>MAX_DEPTH or len(topics)>=MAX_TOPICS: invalid('主题结构无效、超过 1000 个主题或 64 层')
                source_id = t.get('id')
                if not isinstance(source_id,str) or not source_id or source_id in seen: invalid('主题标识缺失或重复')
                seen.add(source_id)
                title = t.get('title','')
                if not isinstance(title,str) or '\x00' in title: invalid()
                body = title
                note = t.get('notes',{})
                if note:
                    if not isinstance(note,dict): invalid()
                    plain = note.get('plain',{})
                    note_text = plain.get('content','') if isinstance(plain,dict) else plain
                    if not note_text and (note.get('html') or note.get('realHTML')):
                        html = note.get('html') or note['realHTML']; note_text = html.get('content','') if isinstance(html,dict) else html
                        warn('富文本备注以原始文本保留，样式见原件')
                    if not note_text and any(note.values()):
                        note_text = json.dumps(note,ensure_ascii=False)
                        warn('未知备注格式以原始文本保留，请结合原件核对')
                    if not isinstance(note_text,str): invalid()
                    body += '\n\n'+note_text
                item = {'parent_index':parent,'sort_order':(order+1)*1024,'title':title.strip()[:200] or ('图片' if t.get('image') else '链接' if t.get('href') else '未命名主题'),
                        'body':body,'depth':depth,'folded':t.get('branch')=='folded'}
                if len(title.strip())>200: warn('长主题在画布显示前 200 字，完整文字保存在正文并参与检索')
                image = t.get('image')
                if image:
                    if not isinstance(image,dict): invalid()
                    item['image'] = resource(image.get('src'))
                    if PurePosixPath(item['image']).suffix.lower() not in ('.png','.jpg','.jpeg','.gif','.webp'): invalid('内嵌图片格式暂不支持')
                href = t.get('href')
                if href:
                    if not isinstance(href,str) or '\x00' in href or len(href)>2048: invalid()
                    if href.startswith('xap:'):
                        item['attachment'] = resource(href)
                        item['body'] += '\n\n内嵌附件：'+PurePosixPath(item['attachment']).name
                    else:
                        item['body'] += '\n\n外部链接（仅保留入口，未读取链接内容）：\n'+href
                        try: item['link'] = CanvasLink(url=href).model_dump()
                        except ValueError: warn('非 HTTP(S) 链接以正文保留，不直接打开')
                labels = t.get('labels',[])
                if labels:
                    if isinstance(labels,list) and all(isinstance(x,str) and 0<len(x.strip())<=50 for x in labels) and len(labels)<=20:
                        item['labels'] = [x.strip() for x in labels]
                    else:
                        item['body'] += '\n\n原始标签：'+json.dumps(labels,ensure_ascii=False)
                        warn('部分标签超出现有标签限制，完整内容保留在正文')
                if any(t.get(k) for k in ('boundaries','relationships','markers','marker-refs','task')):
                    warn('外框、关联线、标记或任务属性未还原到画布，原始信息保留在 Xmind 原件')
                if len(item['body'])>200000 or '\x00' in item['body']: invalid('单个主题正文超过 20 万字或含无效字符')
                total_text += len(item['body'])
                if total_text>2000000: invalid('主题文字总量超过 200 万字')
                index = len(topics); topics.append(item)
                children = t.get('children',{})
                if not isinstance(children,dict) or any(k not in ('attached','detached','summary') for k in children): invalid('存在不支持的主题类型')
                groups = {}
                sibling_order = 0
                for kind in ('attached','detached','summary'):
                    values = children.get(kind,[])
                    if not isinstance(values,list): invalid()
                    groups[kind] = []
                    for c in values:
                        groups[kind].append((c,visit(c,index,sibling_order,depth+1)))
                        sibling_order += 1
                    if kind=='detached' and values: warn('自由主题保留在所属主题下，原始位置见原件')
                summaries = t.get('summaries',[])
                if not isinstance(summaries,list): invalid()
                mapped = set()
                for s in summaries:
                    if not isinstance(s,dict): invalid()
                    match = re.fullmatch(r'\((\d+),(\d+)\)',str(s.get('range','')))
                    target = next((idx for c,idx in groups['summary'] if c.get('id')==s.get('topicId')),None)
                    if not match or target is None or target in mapped: invalid('概要范围或概要主题无效')
                    a,b = map(int,match.groups())
                    if not 0<=a<=b<len(groups['attached']): invalid('概要范围超出分支')
                    topics[target]['summary'] = [idx for _,idx in groups['attached'][a:b+1]]
                    mapped.add(target)
                if any(idx not in mapped for _,idx in groups['summary']): invalid('概要主题缺少对应范围')
                return index
            for sheet_index, sheet in enumerate(sheets):
                if not isinstance(sheet,dict) or 'rootTopic' not in sheet: invalid()
                if sheet.get('relationships'): warn('关联线未还原到画布，原始信息保留在 Xmind 原件')
                visit(sheet['rootTopic'],-1,sheet_index,0)
            # Allocate nonoverlapping subtree bands. Original styling/positions
            # are deliberately not used as authoritative knowledge structure.
            children = {i: [] for i in range(-1,len(topics))}
            for i,t in enumerate(topics): children[t['parent_index']].append(i)
            heights, spans = {}, {}
            for i in reversed(range(len(topics))):
                t=topics[i]
                lines=sum(max(1,math.ceil(len(line)/12)) for line in t['title'].split('\n'))
                heights[i]=60+lines*24+(240 if t.get('image') else 0)+(64 if t.get('link') else 0)+(30 if t.get('labels') else 0)
                spans[i]=max(heights[i],sum(spans[c]+60 for c in children[i])-60)
            def place(i,top):
                topics[i]['x']=topics[i]['depth']*420
                topics[i]['y']=top+(spans[i]-heights[i])/2
                for c in children[i]:
                    place(c,top);top+=spans[c]+60
            top=0
            for i in children[-1]:place(i,top);top+=spans[i]+120
            if top>1000000: invalid('导图尺寸超出现有画布范围')
            if any(t.get('link') for t in topics): warn('外部链接未读取，无法据此检索链接内的视频或文档内容')
            warn('保留主题、正文、图片、附件与概要；原图样式未对齐，原始文件可下载')
            warn('Xmind 原件作为备份；框架与节点先完成，附件独立解析，外部链接保持未解析状态')
            title = topics[0]['title'] if len(sheets)==1 else (str(sheets[0].get('title') or 'Xmind 导入')[:190]+' 等导图')
            return ImportPlan(title,topics,resources,warnings,hashlib.sha256(data).hexdigest())
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,ValueError,TypeError,KeyError,RecursionError,ET.ParseError,UnicodeError,OverflowError,zlib.error,EOFError):
        invalid()


def prepare(plan, original, name, root, user_id, org_id, command_id):
    """Publish immutable private bytes before the atomic registration transaction.

    An uncertain DB outcome leaves content-addressed retry paths intact; no visible
    project or asset registration exists until the transaction commits.
    """
    from uuid import UUID, uuid5, uuid4
    from . import files as storage
    payload = {'title':plan.title,'sha256':plan.sha256,'topics':plan.topics,'report':plan.report(),'files':[]}
    entries = [(0,'source',name,original)]
    for index, topic in enumerate(plan.topics):
        for kind in ('image','attachment'):
            if topic.get(kind):
                path = topic[kind]
                entries.append((index,kind,PurePosixPath(path).name,plan.resources[path]))
    for index, kind, filename, data in entries:
        key = str(index)+':'+kind
        file_command = uuid5(UUID(str(command_id)),key)
        vid = storage.version_id(user_id,org_id,file_command)
        target = storage.private_path(root,org_id,user_id,vid,create=True)
        staged = target.parent / ('.pending-'+str(uuid4()))
        try:
            with staged.open('xb') as stream:
                stream.write(data)
            # Original archive already passed stricter bounds in parse(). Its
            # unsupported status is honest: archive bytes are a backup, not OCR.
            metadata = storage.inspect_file(staged,filename,extract_text=False)
            if kind=='image' and not metadata['mime_type'].startswith('image/'):
                invalid('图片内容与格式不符')
            if kind=='image':
                from PIL import Image, UnidentifiedImageError
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width*image.height>16000000: invalid('内嵌图片超过 1600 万像素，无法安全解析')
                        plan.topics[index]['image_aspect']=max(0.01,min(100,image.width/image.height))
                        image.verify()
                except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
                    invalid('内嵌图片损坏或尺寸超过解析范围')
            storage.publish_file(staged,target,metadata['sha256'])
            payload['files'].append({'key':key,'topic_index':index,'command_id':str(file_command),
                                     'version_id':vid,'metadata':metadata})
        finally:
            staged.unlink(missing_ok=True)
    return payload
