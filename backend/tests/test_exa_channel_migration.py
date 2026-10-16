import importlib.util
from pathlib import Path
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location('exa_channel_migration', Path(__file__).parents[2] / 'deploy/enable-exa-search.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_enables_configured_key_without_changing_other_settings(tmp_path):
    env = tmp_path / '.env'
    env.write_text('AGENT_REACH_PLATFORMS=web,github,xiaohongshu\nAGENT_REACH_EXA_API_KEY=test\nOTHER=keep\n')
    env.chmod(0o600)
    marker = tmp_path / 'migrations' / 'exa'
    module.enable(env, marker)
    values = dotenv_values(env)
    assert values['AGENT_REACH_PLATFORMS'] == 'web,github,xiaohongshu,exa'
    assert values['OTHER'] == 'keep'
    assert env.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / '.env.before-exa-search-enable').exists()
    # An explicit later disable must remain disabled on subsequent deployments.
    env.write_text('AGENT_REACH_PLATFORMS=web\nAGENT_REACH_EXA_API_KEY=test\n')
    module.enable(env, marker)
    assert dotenv_values(env)['AGENT_REACH_PLATFORMS'] == 'web'


def test_does_not_enable_without_key(tmp_path):
    env = tmp_path / '.env'; env.write_text('AGENT_REACH_PLATFORMS=web\n')
    marker = tmp_path / 'marker'
    module.enable(env, marker)
    assert dotenv_values(env)['AGENT_REACH_PLATFORMS'] == 'web'
    assert not marker.exists()
