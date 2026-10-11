"""Deployment readiness cannot be inferred from enabled flags alone."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from services.agent.agent_reach.preflight import configuration_checks, inspect_runtime, runtime_requirements


def settings(tmp_path,**values):
    defaults=dict(agent_reach_enabled=False,agent_reach_bin_dir=str(tmp_path/'bin'),
        agent_reach_google_client_id=None,agent_reach_google_client_secret=None,agent_reach_google_redirect_uri=None,
        agent_reach_exa_api_key=None,agent_reach_xhs_slots_json='{}',agent_reach_xhs_origins_json='{}',
        agent_reach_write_actions='',agent_reach_media_staging_dir=str(tmp_path/'media'))
    return SimpleNamespace(**{**defaults,**values})


def test_missing_config_redacted_and_no_directories_created(tmp_path):
    config=settings(tmp_path,agent_reach_google_client_secret='sensitive-test-value')
    report=configuration_checks(config,{'youtube','xiaohongshu','exa'})
    states={row['name']:row['state'] for row in report}
    assert states['runtime']==states['youtube.oauth']==states['xiaohongshu.slots']==states['exa.api_key']=='blocked'
    assert 'sensitive-test-value' not in json.dumps(report)
    assert not (tmp_path/'media').exists()


def test_lockfile_parses_versions_and_commits(tmp_path):
    lock=tmp_path/'freeze'; lock.write_text('qrcode==8.2\nagent-reach @ git+https://example.com/repo.git@abc123\n')
    assert runtime_requirements(lock)=={'qrcode':{'version':'8.2'},'agent-reach':{'commit':'abc123'}}


def test_missing_runtime_does_not_spawn_process(tmp_path,monkeypatch):
    import services.agent.agent_reach.preflight as module
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:pytest.fail('must not execute absent runtime'))
    assert inspect_runtime(str(tmp_path),tmp_path/'absent')[0] is False


@pytest.mark.parametrize('actual,ok',[({'qrcode':{'version':'8.2'}},True),({'qrcode':{'version':'old'}},False)])
def test_runtime_version_checks_are_exact_and_environment_isolated(tmp_path,monkeypatch,actual,ok):
    import services.agent.agent_reach.preflight as module
    for name in ('python','agent-reach','twitter','bili','rdt','yt-dlp'):
        file=tmp_path/name; file.touch(); file.chmod(0o700)
    lock=tmp_path/'freeze'; lock.write_text('qrcode==8.2\n')
    def run(command,**kwargs):
        assert command[1]=='-I' and 'DATABASE_URL' not in kwargs['env']
        assert Path(kwargs['cwd']).is_dir()
        return SimpleNamespace(returncode=0,stdout=json.dumps(actual))
    monkeypatch.setattr(module.subprocess,'run',run)
    assert inspect_runtime(str(tmp_path),lock)[0] is ok


def test_invalid_write_action_and_world_readable_media_rejected(tmp_path):
    media=tmp_path/'media'; media.mkdir(mode=0o755)
    report=configuration_checks(settings(tmp_path,agent_reach_write_actions='twitter:create_post,github:create_post'),{'twitter','github'})
    assert any(row['name']=='write_action' and row['state']=='blocked' for row in report)
    assert next(row for row in report if row['name']=='media.directory')['state']=='blocked'
