"""Credential provisioning must preserve existing production settings and tokens."""
import importlib.util
from pathlib import Path

from dotenv import dotenv_values
import pytest

spec = importlib.util.spec_from_file_location('kie_mcp_provisioning', Path(__file__).resolve().parents[2] / 'deploy/configure-kie-mcp.py')
provisioning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provisioning)


@pytest.fixture
def production(tmp_path):
    root = tmp_path / 'app'
    (root / 'backend').mkdir(parents=True)
    lock = Path(str(root) + '.release-lock')
    lock.mkdir()
    (lock / 'owner').write_text('test-release-owner')
    env = root / 'backend/.env'
    env.write_text('KIE_API_KEY=example-key\nOTHER_SETTING=preserved\n')
    return root, env


def test_provision_creates_private_token_preserves_config_and_reuses(production):
    root, env = production
    assert provisioning.configure(root, 'test-release-owner') == 'configured'
    values = dotenv_values(env)
    assert len(values['KIE_MCP_TOKEN']) >= 32
    assert values['KIE_API_KEY'] == 'example-key'
    assert values['OTHER_SETTING'] == 'preserved'
    assert env.stat().st_mode & 0o777 == 0o600
    assert provisioning.configure(root, 'test-release-owner') == 'already_configured'
    assert dotenv_values(env)['KIE_MCP_TOKEN'] == values['KIE_MCP_TOKEN']
    assert len(list(env.parent.glob('.env.backup.patch.*'))) == 1


def test_wrong_lock_owner_never_changes_configuration(production):
    root, env = production
    before = env.read_bytes()
    with pytest.raises(ValueError, match='LOCK_REQUIRED'):
        provisioning.configure(root, 'another-owner')
    assert env.read_bytes() == before


def test_existing_short_token_requires_explicit_repair(production):
    root, env = production
    env.write_text(env.read_text() + 'KIE_MCP_TOKEN=short\n')
    with pytest.raises(ValueError, match='TOO_SHORT'):
        provisioning.configure(root, 'test-release-owner')
    assert dotenv_values(env)['KIE_MCP_TOKEN'] == 'short'


def test_missing_vendor_key_does_not_enable_endpoint(production):
    root, env = production
    env.write_text('OTHER_SETTING=preserved\n')
    assert provisioning.configure(root, 'test-release-owner') == 'skipped_no_kie_key'
    assert 'KIE_MCP_TOKEN' not in dotenv_values(env)
