"""Activation must fail closed before editing production configuration."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def activation():
    path=Path(__file__).resolve().parents[2]/'deploy/enable-knowledge.py'
    spec=importlib.util.spec_from_file_location('kb_activation',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_activation_requires_matching_production_lock(activation,tmp_path,monkeypatch):
    root=tmp_path/'app';owner=tmp_path/'app.release-lock';owner.mkdir();(owner/'owner').write_text('expected')
    monkeypatch.setattr(activation,'ROOT',root)
    with pytest.raises(ValueError,match='LOCK_REQUIRED'):activation.check_lock('wrong')
    activation.check_lock('expected')


def test_missing_decoder_or_install_failure_prevents_activation(activation,monkeypatch):
    monkeypatch.setattr(activation.shutil,'which',lambda name:'/usr/bin/'+name)
    monkeypatch.setattr(activation.Path,'read_text',lambda self:'ID="alinux"\nVERSION_ID="3"')
    calls=[]
    def execute(args,**kwargs):
        calls.append(args)
        if args[0]=='ffprobe':raise RuntimeError('missing decoder')
    monkeypatch.setattr(activation.subprocess,'run',execute)
    with pytest.raises(RuntimeError,match='missing decoder'):activation.install()
    assert ['dnf','-y','install','ffmpeg','poppler-utils'] in calls


def test_repository_checksum_failure_does_not_install(activation,monkeypatch):
    monkeypatch.setattr(activation.shutil,'which',lambda name:'/usr/bin/dnf' if name=='dnf' else None)
    monkeypatch.setattr(activation.Path,'read_text',lambda self:'ID="alinux"\nVERSION_ID="3"')
    from io import BytesIO
    monkeypatch.setattr(activation.urllib.request,'urlopen',lambda *a,**kw:BytesIO(b'wrong package'))
    monkeypatch.setattr(activation.subprocess,'run',lambda *a,**kw:pytest.fail('must not install unverified package'))
    with pytest.raises(ValueError,match='CHECKSUM_MISMATCH'):activation.install()


def test_activation_remains_explicit_and_precedes_backend_restart():
    root=Path(__file__).resolve().parents[2]
    script=(root/'deploy/deploy.sh').read_text()
    start=script.index('deploy_backend() {');end=script.index('pkill -f kernel_worker',start)
    assert 'if [ "$ENABLE_KNOWLEDGE" = true ]' in script[start:end]
    assert 'enable-knowledge.py --lock-token "$EVERYDAYAI_RELEASE_LOCK_TOKEN"' in script[start:end]
