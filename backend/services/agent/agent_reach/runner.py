"""Fixed executables, isolated homes, bounded pipes and process-group cleanup."""
import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

from .contracts import ReachError


class ToolRunner:
    def __init__(self, bin_dir: str):
        self.bin_dir = Path(bin_dir).resolve()

    async def run(self, name, arguments, *, env=None, files=None, input_json=None):
        if name not in {'twitter', 'bili', 'rdt', 'yt-dlp', 'reddit-bridge', 'bili-bridge', 'qr-image'}:
            raise ReachError('UNSUPPORTED_OPERATION', '不支持该执行工具')
        executable = self.bin_dir / ('python' if (name.endswith('-bridge') or name == 'qr-image') else name)
        if (name.endswith('-bridge') or name == 'qr-image'):
            script = {'reddit-bridge': 'reddit_bridge.py', 'bili-bridge': 'bili_bridge.py', 'qr-image': 'qr_image.py'}[name]
            arguments = ['-I', str(Path(__file__).with_name(script)), *arguments]
        if not executable.is_file():
            raise ReachError('CONFIG_REQUIRED', f'{name}渠道尚未安装')
        with tempfile.TemporaryDirectory(prefix='agent-reach-') as home:
            for relative, content in (files or {}).items():
                path = Path(home) / relative
                if not path.resolve().is_relative_to(Path(home)):
                    raise ReachError('CONFIG_REQUIRED', '工具配置路径无效')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_text(content, encoding='utf-8')
                path.chmod(0o600)
            process_env = {'HOME': home, 'XDG_CONFIG_HOME': home + '/.config',
                           'PATH': str(self.bin_dir) + ':/usr/bin:/bin',
                           'LANG': 'en_US.UTF-8', 'OUTPUT': 'json',
                           'PYTHONIOENCODING': 'utf-8', **(env or {})}
            process = await asyncio.create_subprocess_exec(
                str(executable), *arguments, cwd=home, env=process_env,
                stdin=asyncio.subprocess.PIPE if input_json is not None else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            reader = asyncio.create_task(self._read(process.stdout))
            try:
                if input_json is not None:
                    encoded = json.dumps(input_json).encode()
                    if len(encoded) > 64000:
                        raise ReachError('INVALID_ARGUMENTS', '调用资料超过上限')
                    process.stdin.write(encoded)
                    await process.stdin.drain()
                    process.stdin.close()
                output = await reader
                await process.wait()
                try:
                    payload = json.loads(output)
                except (ValueError, UnicodeError):
                    raise ReachError('UPSTREAM_CHANGED', '上游工具未返回有效JSON资料') from None
                if process.returncode or (isinstance(payload, dict) and payload.get('ok') is False):
                    error = payload.get('error') if isinstance(payload, dict) else None
                    code = str(error.get('code', '')) if isinstance(error, dict) else ''
                    mapped = {'not_authenticated': 'AUTH_EXPIRED', 'rate_limited': 'RATE_LIMITED',
                              'forbidden': 'PLATFORM_BLOCKED', 'permission_denied': 'PLATFORM_BLOCKED',
                              'not_found': 'NOT_FOUND', 'write_uncertain': 'WRITE_UNCERTAIN'}.get(code, 'UPSTREAM_CHANGED')
                    raise ReachError(mapped, '平台操作未完成，请检查渠道状态')
                return payload
            finally:
                # Even a tool that exited may have left a child running.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if not reader.done():
                    reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
                await process.wait()

    @staticmethod
    async def _read(stream):
        output = bytearray()
        while chunk := await stream.read(65536):
            output.extend(chunk)
            if len(output) > 2_000_000:
                raise ReachError('OUTPUT_LIMIT', '工具输出超过单次资料上限')
        return output
