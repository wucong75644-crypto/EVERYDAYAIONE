import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('xhs_install', Path(__file__).parents[2]/'deploy/xhs/install-login-fix.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

@pytest.mark.parametrize('mode', ['success','bad_candidate','restart_failure'])
def test_install_verified_candidate_and_rollback(tmp_path, monkeypatch, mode):
    root = tmp_path/'runtime'; root.mkdir()
    assets = tmp_path/'assets'; assets.mkdir()
    target = root/'xiaohongshu-mcp'; target.write_bytes(b'old')
    candidate = root/'login-fix-candidate/xiaohongshu-mcp'; candidate.parent.mkdir(); candidate.write_bytes(b'new')
    patch = assets/'login-browser.patch'; patch.write_bytes(b'patch')
    (assets/'login-fix-artifact.json').write_text(json.dumps({'sha256':module.digest(candidate),'patch_sha256':module.digest(patch)}))
    if mode == 'bad_candidate': candidate.write_bytes(b'unverified')
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module, '__file__', str(assets/'install-login-fix.py'))
    monkeypatch.setattr(module, 'dotenv_values', lambda _: {'AGENT_REACH_XHS_SLOTS_JSON':json.dumps({'org':[{'connection_id':'00000000-0000-0000-0000-000000000001','origin':'http://127.0.0.1:18061'}]})})
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if mode == 'restart_failure' and args[1] == 'restart' and kwargs.get('check'):
            raise RuntimeError('restart failed')
    monkeypatch.setattr(module.subprocess, 'run', run)
    class Health:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: Health())
    if mode != 'success':
        with pytest.raises(RuntimeError): module.main()
        assert target.read_bytes() == b'old'
        if mode == 'bad_candidate': assert not calls
    else:
        module.main()
        assert target.read_bytes() == b'new'
        assert list(root.glob('*.backup-*'))[0].read_bytes() == b'old'
        count = len(calls)
        module.main()
        assert len(calls) == count
