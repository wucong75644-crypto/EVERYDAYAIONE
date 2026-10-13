"""Publish/bind/restore each actual social method against PostgreSQL and NAS."""
import pytest

from services.skills.authoring_contracts import CreateSkill
from services.skills.scheduled import bind_scheduled_skills, validate_scheduled_skills, scheduled_skill_options
from services.social_ops.skill_bundle import bundle_specs
from tests.test_scheduled_skill_postgres import configured, environment, postgres_socket  # noqa: F401
from tests.test_skill_authoring_postgres import publish


@pytest.mark.parametrize('legacy_metadata', [True, False])
async def test_all_ten_methods_still_bind_and_restore_without_revision_rewrites(configured, legacy_metadata):
    env = configured
    service = env.service()
    for spec in bundle_specs():
        content = spec.content
        if legacy_metadata:
            content = content.model_copy(update={'catalog_metadata': content.catalog_metadata.model_copy(update={
                'execution_modes': ('interactive', 'scheduled'), 'model_selectable': True})})
        pid = service.create(CreateSkill(skill_key=spec.skill_key, content=content))['package_id']
        revision = publish(service, pid)['draft']['revision']
        pins = await bind_scheduled_skills(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task),
            selections=[{'skill_id': spec.skill_key, 'revision': revision}])
        assert pins['skills'][0]['candidate']['revision'] == revision
        assert pins['skills'][0]['candidate']['catalog_metadata']['model_selectable'] is legacy_metadata
        await validate_scheduled_skills(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task), snapshot=pins)
    options = await scheduled_skill_options(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task))
    assert {s.skill_key for s in bundle_specs()} <= {o['skill_id'] for o in options}
