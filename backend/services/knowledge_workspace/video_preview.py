"""Recomputable, bounded playback renditions. Originals and database rows are immutable."""
from collections import OrderedDict
from pathlib import Path
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from . import files, media_delivery

_cache = OrderedDict()
_cache_lock = threading.Lock()
_encoder = threading.Lock()
CACHE_BYTES = 64 * 1024 * 1024
PROFILE = 'h264-720p-2500k-v1'


def run(args, timeout=35):
    return subprocess.run([sys.executable,str(Path(__file__).with_name('media_limits.py')),*args],
                          check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout).stdout


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
                          '-show_format','-show_streams','-of','json',str(path)],10))


def prepare(root, version):
    # Verify even cache hits; a rendition never turns stale metadata into authority.
    source = media_delivery.verified_file(root,version)
    key = (PROFILE,version['sha256'])
    try:
        with _cache_lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key]
        if not _encoder.acquire(blocking=False):
            files.fail('KNOWLEDGE_PREVIEW_BUSY','正在准备其他视频，请稍后重试或选择原画',429)
        try:
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                files.fail('KNOWLEDGE_PREVIEW_UNAVAILABLE','流畅预览暂不可用，请选择原画',503)
            with tempfile.TemporaryDirectory(prefix='kb-playback-') as directory:
                original=Path(directory)/'source.mp4';target=Path(directory)/'preview.mp4'
                with original.open('wb') as output:shutil.copyfileobj(source,output,256*1024)
                info=probe(original);duration=float(info.get('format',{}).get('duration',0))
                video=next((s for s in info.get('streams',[]) if s.get('codec_type')=='video'),{})
                audio=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
                if not 0<duration<=86400 or not video or int(video.get('width',0))*int(video.get('height',0))>16000000:
                    files.fail('KNOWLEDGE_PREVIEW_UNSUPPORTED','此视频暂不适合生成流畅预览，请选择原画',422)
                bitrate=version['size']*8/duration
                item={'data':None,'size':version['size'],'sha256':version['sha256'],'duration':duration}
                if bitrate>2800000 or max(int(video.get('width',0)),int(video.get('height',0)))>1280 or video.get('codec_name')!='h264' or any(s.get('codec_name')!='aac' for s in audio):
                    if duration>180:files.fail('KNOWLEDGE_PREVIEW_UNSUPPORTED','此视频生成预览耗时较长，请选择原画',422)
                    scale="scale=w='min(iw,if(gte(iw,ih),1280,720))':h='min(ih,if(gte(iw,ih),720,1280))':force_original_aspect_ratio=decrease:force_divisible_by=2"
                    run(['ffmpeg','-v','error','-nostdin','-threads','1','-protocol_whitelist','file,pipe','-i',str(original),
                         '-map','0:v:0','-map','0:a:0?','-sn','-dn','-filter_threads','1','-vf',scale,
                         '-c:v','libx264','-threads','1','-preset','fast','-crf','23','-maxrate','2500k','-bufsize','5000k',
                         '-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-fs',str(files.MAX_BYTES),'-movflags','+faststart',str(target)])
                    converted=probe(target);result_duration=float(converted.get('format',{}).get('duration',0))
                    if abs(result_duration-duration)>max(.25,duration*.01) or not 0<target.stat().st_size<=files.MAX_BYTES:
                        files.fail('KNOWLEDGE_PREVIEW_INVALID','流畅预览校验失败，请选择原画',503)
                    data=target.read_bytes()
                    item={'data':data,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'duration':result_duration}
            with _cache_lock:
                _cache[key]=item
                while len(_cache)>16 or sum(len(v['data'] or b'') for v in _cache.values())>CACHE_BYTES:
                    _cache.popitem(last=False)
            return item
        except (subprocess.SubprocessError,OSError,ValueError,KeyError,StopIteration):
            files.fail('KNOWLEDGE_PREVIEW_FAILED','流畅预览生成失败，请稍后重试或选择原画',503)
        finally:_encoder.release()
    finally:source.close()
