"""Private immutable bytes; canonical registration and references live in PostgreSQL."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import UUID, uuid5, NAMESPACE_URL
import zipfile

from core.exceptions import AppException
from services.assets.file_identity import identify_file

MAX_BYTES = 20 * 1024 * 1024
TEXT_EXTENSIONS = {'.txt','.md','.csv','.tsv','.json','.yaml','.yml','.xml','.log','.py','.js','.ts','.html','.css','.sql'}
BINARY_MIMES = {
    '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif', '.webp':'image/webp', '.mp4':'video/mp4',
}
DOWNLOAD_EXTENSIONS = {'.doc','.xls','.ppt','.pptx','.zip','.xmind'}
PUBLIC_VERSION_KEYS = {'binding_id','revision','version_id','asset_id','name','mime_type','size','sha256','text_status','created_at','parse_state'}


def fail(code, message, status=422):
    raise AppException(code=code, message=message, status_code=status)


def public_version(value):
    return {k:v for k,v in value.items() if k in PUBLIC_VERSION_KEYS}


def clean_name(name):
    name = str(name or '').replace('\\', '/').split('/')[-1]
    name = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', '', name).strip(' .')
    if not name or len(name)>180:
        fail('KNOWLEDGE_INVALID_FILENAME','文件名不能为空且最多 180 个字符')
    extension = Path(name).suffix.lower()
    if extension not in TEXT_EXTENSIONS | BINARY_MIMES.keys() | DOWNLOAD_EXTENSIONS:
        fail('KNOWLEDGE_UNSUPPORTED_FILE','不支持此文件类型，请使用文档、表格、PDF、图片或 MP4')
    return name


def version_id(user_id, org_id, command_id):
    return str(uuid5(NAMESPACE_URL, f'knowledge:{UUID(str(user_id))}:{str(UUID(str(org_id))) if org_id else "personal"}:{UUID(str(command_id))}'))


def private_path(root, org_id, owner, version, *, create=False):
    # No user-supplied path is ever used in the private namespace.
    base = Path(root).resolve()
    if create: base.mkdir(parents=True, mode=0o700, exist_ok=True)
    parts = ['.knowledge-versions', str(UUID(str(org_id))) if org_id else 'personal', str(UUID(str(owner))), str(UUID(str(version)))]
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail('KNOWLEDGE_FILE_UNAVAILABLE','文件存储不可用',503)
        if create: current.mkdir(mode=0o700, exist_ok=True)
        if current.exists() and not current.is_dir():
            fail('KNOWLEDGE_FILE_UNAVAILABLE','文件存储不可用',503)
    return current / 'content'


def inspect_file(path, name, *, extract_text=True):
    name = clean_name(name); extension = Path(name).suffix.lower()
    data = Path(path).read_bytes()
    if not data or len(data)>MAX_BYTES:
        fail('KNOWLEDGE_FILE_SIZE','文件不能为空且不能超过 20MB',413)
    text = ''; status = 'unsupported'
    if extension in TEXT_EXTENSIONS:
        mime='text/plain'
        if extract_text:
            try:text=data.decode('utf-8-sig')
            except UnicodeDecodeError:fail('KNOWLEDGE_FILE_ENCODING','文本文件请使用 UTF-8 编码')
            if '\x00' in text:fail('KNOWLEDGE_INVALID_FILE','文本文件包含无效字符')
            status='truncated' if len(text)>200000 else 'ready';text=text[:200000]

    else:
        if extension in {'.docx','.xlsx','.pptx','.zip','.xmind'}:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    infos = archive.infolist()
                    if len(infos)>2000 or sum(i.file_size for i in infos)>50*1024*1024 or any(i.flag_bits & 1 for i in infos):
                        fail('KNOWLEDGE_ARCHIVE_LIMIT','压缩内容过大、包含过多文件或已加密，无法安全预览')
            except zipfile.BadZipFile: fail('KNOWLEDGE_INVALID_FILE','文件内容与格式不符')
        mime = BINARY_MIMES.get(extension, 'application/octet-stream')
        if extension in BINARY_MIMES and identify_file(data,stable_id='kb',provider_name=name).detected_mime_type != mime:
            fail('KNOWLEDGE_INVALID_FILE','文件内容与扩展名不符')
        if extract_text and extension in {'.pdf','.docx','.xlsx'}:
            try:
                worker = Path(__file__).with_name('file_extract.py')
                result = subprocess.run([sys.executable,str(worker),str(Path(path).resolve()),extension],
                                        capture_output=True,timeout=15,check=True)
                parsed = json.loads(result.stdout)
                text, status = parsed['extracted_text'], parsed['text_status']
            except (subprocess.SubprocessError, ValueError, KeyError): status = 'failed'
    return {'name':name,'mime_type':mime,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'extracted_text':text,'text_status':status}


def publish_file(staged, target, digest):
    """Idempotent immutable publication. Leave uncertain DB outcomes for explicit retry."""
    with open(staged, 'rb') as stream: os.fsync(stream.fileno())
    try:
        os.link(staged, target)
        os.chmod(target,0o400)
        fd = os.open(target.parent,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except FileExistsError:
        existing = read_regular(target)
        if hashlib.sha256(existing).hexdigest() != digest:
            fail('IDEMPOTENCY_PAYLOAD_CONFLICT','此重试标识已用于不同文件，请重新选择文件',409)


def read_regular(path):
    try:
        fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_BYTES:
                fail('KNOWLEDGE_FILE_UNAVAILABLE','文件暂时不可用',503)
            return stream.read(MAX_BYTES+1)
    except OSError:
        fail('KNOWLEDGE_FILE_UNAVAILABLE','文件暂时不可用，请保留版本信息并联系管理员',503)


def read_version(root, version):
    target = private_path(root,version['storage_org'],version['storage_owner'],version['version_id'])
    content = read_regular(target)
    if len(content)!=version['size'] or hashlib.sha256(content).hexdigest()!=version['sha256']:
        fail('KNOWLEDGE_FILE_INTEGRITY','文件校验失败，已阻止读取',503)
    return content
