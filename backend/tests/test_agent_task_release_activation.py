"""Actual deployment functions with remote IO captured; no production/model calls."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values
from tests.test_production_env_safety import ENV

ROOT=Path(__file__).resolve().parents[2]


def invoke(tmp_path,*,enabled=True,failure=0):
    source=(ROOT/'deploy/deploy.sh').read_text().rsplit('main "$@"',1)[0]
    stubs='''
remote_exec() {
  printf '%s\\n' "$*" >> "$TRACE"
  if [[ "$1" == bash ]]; then cat >/dev/null; return 0; fi
  cat > "$PATCH"
  return "$FAILURE"
}
ENABLE_KNOWLEDGE=false
ENABLE_AGENT_TASK_EXECUTION=$ENABLED
EVERYDAYAI_RELEASE_LOCK_TOKEN=owned-test-token
deploy_backend
'''
    result=subprocess.run(['bash','-s'],input=source+stubs,text=True,capture_output=True,cwd=ROOT,
        env={**os.environ,'TRACE':str(tmp_path/'trace'),'PATCH':str(tmp_path/'patch'),'ENABLED':'true' if enabled else 'false','FAILURE':str(failure)},timeout=10)
    return result,(tmp_path/'trace').read_text().splitlines()


def test_explicit_activation_patches_only_two_keys_between_prepare_and_restart(tmp_path):
    result,trace=invoke(tmp_path)
    assert result.returncode==0
    assert len(trace)==3 and trace[0]==trace[2]=='bash'
    assert trace[1].endswith('deploy/env-patch.py --lock-token owned-test-token')
    patch=json.loads((tmp_path/'patch').read_text())
    assert patch=={'AGENT_TASK_EXECUTION_ENABLED':'true','AGENT_TASK_ERP_WRITE_ENABLED':'false'}
    target=tmp_path/'.env';target.write_text('DATABASE_URL=unchanged-db\nAPI_KEY=unchanged-secret\nOTHER_FEATURE=true\nAGENT_TASK_ERP_WRITE_ENABLED=true\n')
    before=target.read_bytes();ENV.apply_patch_file(target,patch)
    assert dotenv_values(target)=={'DATABASE_URL':'unchanged-db','API_KEY':'unchanged-secret','OTHER_FEATURE':'true',**patch}
    backups=list(tmp_path.glob('.env.backup.patch.*'))
    assert len(backups)==1 and backups[0].read_bytes()==before
    assert backups[0].stat().st_mode&0o777==0o600


def test_default_release_does_not_patch_flags(tmp_path):
    result,trace=invoke(tmp_path,enabled=False)
    assert result.returncode==0 and trace==['bash','bash']
    assert not (tmp_path/'patch').exists()


def test_configuration_failure_stops_before_service_restart(tmp_path):
    result,trace=invoke(tmp_path,failure=42)
    assert result.returncode==42 and len(trace)==2


@pytest.mark.parametrize('mode',[['--frontend-only'],['--accept-and-close'],['--rollback','0'*40],['--recover-lock','not-owner','--failed-commit','0'*40]])
def test_activation_rejects_wrong_release_mode_before_any_production_io(mode):
    result=subprocess.run(['bash',str(ROOT/'deploy/release.sh'),'--enable-agent-task-execution',*mode],cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and '只用于包含后端的正常发布' in result.stdout+result.stderr
