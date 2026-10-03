from pathlib import Path


def test_skill_service_override_grants_only_org_and_personal_subtrees_write_access():
    config = (Path(__file__).resolve().parents[2] / 'deploy' /
              'everydayai-backend-skill-authoring.conf').read_text()

    assert 'RequiresMountsFor=/mnt/platform-skills/org /mnt/platform-skills/personal' in config
    assert 'ReadWritePaths=/mnt/platform-skills/org /mnt/platform-skills/personal' in config
    assert 'ReadWritePaths=/mnt/platform-skills\n' not in config
