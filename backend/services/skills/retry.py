"""Server-owned user Skill intent for new attempts, separate from authority."""

import asyncio
import json
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator

from services.skills.contracts import Contract, SkillTaskMode, SkillKey
from services.skills.selection import SkillSelection


class SkillIntent(Contract):
    task_mode: SkillTaskMode
    selected_skill: SkillSelection | None = None
    session_skills: tuple[SkillSelection, ...] = Field(default=(), max_length=4)

    package_ids: dict[SkillKey, UUID] = Field(default_factory=dict, max_length=5)

    @model_validator(mode='after')
    def unique_bindings(self):
        if len({s.skill_id for s in self.session_skills}) != len(self.session_skills):
            raise ValueError('duplicate session Skill')
        keys = {s.skill_id for s in self.session_skills}
        if self.selected_skill:
            keys.add(self.selected_skill.skill_id)
        if not set(self.package_ids) <= keys:
            raise ValueError('unexpected package identity')
        return self

    @property
    def required(self):
        return bool(self.selected_skill or self.session_skills)


def parse_intent(raw):
    try:
        return SkillIntent.model_validate(raw)
    except (TypeError, ValueError):
        raise HTTPException(409, '原任务的 Skill 记录不完整，请重新选择后发送新任务') from None


def _object(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise HTTPException(409, '原任务记录无法读取，请重新选择后发送新任务') from None
    return raw if isinstance(raw, dict) else {}


async def original_intent(db, *, user_id, org_id, conversation_id, message_id, task_mode, params_to_restore=None):
    """Authenticate first; HTTP selections/private params never override history."""
    from services.skills.available import _conversation_org
    org = await asyncio.to_thread(_conversation_org, db, user_id, conversation_id)
    if org != org_id:
        raise HTTPException(403, '原任务的组织与当前会话不一致')
    if not message_id:
        raise HTTPException(400, '重试或重新生成必须指定原消息')

    def read():
        result = (db.table('messages').select('id,conversation_id,role,generation_params')
                  .eq('id', message_id).eq('conversation_id', conversation_id).maybe_single().execute())
        row = result.data if result and isinstance(result.data, dict) else None
        if not row or row.get('role') != 'assistant':
            raise HTTPException(404, '原任务不存在或无权访问')
        params = _object(row.get('generation_params'))
        saved = parse_intent(params['_skill_intent']) if '_skill_intent' in params else None
        # Pre-fix media/Actor tasks already stored explicit selection and media
        # activation identities. Never reconstruct a body or trust model output.
        query = (db.table('tasks').select('id,type,request_params').eq('placeholder_message_id', message_id)
                 .eq('conversation_id', conversation_id).eq('user_id', user_id))
        query = query.is_('org_id', 'null') if org_id is None else query.eq('org_id', org_id)
        result = query.order('created_at', desc=True).limit(1).execute()
        rows = result.data if result and isinstance(result.data, list) else []
        if params_to_restore is not None and (saved.task_mode if saved else task_mode) == 'image-ecom':
            original_params = _object(rows[0].get('request_params')) if rows else {}
            for key in ('image_task_meta', '_batch_prompts', 'product_image_urls', 'style_ref_urls'):
                params_to_restore.pop(key, None)
                if key in original_params:
                    params_to_restore[key] = original_params[key]
        if saved is not None:
            return saved
        if not rows and '_selected_skill' not in params:
            raise HTTPException(409, '原任务缺少 Skill 记录，请重新选择后发送新任务')
        params = _object(rows[0].get('request_params')) if rows else params
        if '_skill_intent' in params:
            return parse_intent(params['_skill_intent'])
        raw = {'task_mode': params.get('_skill_task_mode', task_mode),
               'selected_skill': params.get('_selected_skill'), 'session_skills': []}
        for item in params.get('_media_skills', []):
            if not isinstance(item, dict):
                raise HTTPException(409, '原任务的 Skill 记录不完整')
            if item.get('selection') == 'user' and raw['selected_skill'] is None:
                raw['selected_skill'] = {k: item.get(k) for k in ('skill_id', 'revision')}
            if item.get('selection') == 'session':
                raw['session_skills'].append({k: item.get(k) for k in ('skill_id', 'revision')})
        if rows and rows[0].get('type') == 'chat':
            checkpoint = (db.table('conversation_turn_checkpoints').select('state')
                          .eq('task_id', rows[0]['id']).eq('conversation_id', conversation_id)
                          .maybe_single().execute())
            row = checkpoint.data if checkpoint and isinstance(checkpoint.data, dict) else None
            if row is None:
                raise HTTPException(409, '原聊天任务缺少 Skill 快照，请重新选择后发送新任务')
            state = _object(_object(row.get('state')).get('skill_runtime'))
            directory = {c['skill_key']: c for c in state.get('directory', [])}
            raw['session_skills'] = [{'skill_id': key, 'revision': directory[key]['revision']}
                                    for key in state.get('session_skill_ids', [])]
            if state.get('manual_skill_id') and raw['selected_skill'] is None:
                key = state['manual_skill_id']
                raw['selected_skill'] = {'skill_id': key, 'revision': directory[key]['revision']}
            raw['package_ids'] = {s['skill_id']: directory[s['skill_id']]['package_id']
                                  for s in raw['session_skills'] if 'package_id' in directory[s['skill_id']]}
            manual = raw['selected_skill']
            if manual and 'package_id' in directory.get(manual['skill_id'], {}):
                raw['package_ids'][manual['skill_id']] = directory[manual['skill_id']]['package_id']
        # An empty historical intent stays empty, even if new pins now exist.
        return parse_intent(raw)

    try:
        return await asyncio.to_thread(read)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, '无法确认原任务的 Skill，请稍后重试') from None


async def capture_intent(handler, *, user_id, conversation_id, task_mode, selection):
    from core.config import get_settings
    from services.skills.runtime_source import ActorSkillSource
    from services.tools.context import ToolContext
    settings = get_settings()
    bindings, packages = [], {}
    if settings.skill_catalog_enabled is True and getattr(handler, 'org_id', None) is not None:
        context = ToolContext(actor_user_id=user_id, workspace_owner_id=user_id, org_id=handler.org_id,
                              context_scope='user', personal_context_allowed=True, agent_domain='general',
                              permission_mode='auto', execution_mode='interactive',
                              authorized_tool_names=frozenset(), conversation_id=conversation_id)
        try:
            source = ActorSkillSource(handler, context, settings, task_mode=task_mode)
            bindings = await source.session_bindings()
            if selection:
                for candidate in await source.discover():
                    if candidate.skill_key == selection.skill_id and candidate.revision == selection.revision:
                        packages[candidate.skill_key] = candidate.package_id
            # Runtime directory gives an existing session pin precedence.
            packages.update({c.skill_key: c.package_id for c in bindings})
        except Exception:
            raise HTTPException(409, '无法确认会话 Skill，请检查设置后重试') from None
    return parse_intent({'task_mode': task_mode, 'selected_skill': selection,
                         'session_skills': [{'skill_id': c.skill_key, 'revision': c.revision} for c in bindings],
                         'package_ids': packages})


class PinnedIntentSource:
    """Pin identities but recheck the current grant, revision and mode on load."""
    def __init__(self, source, intent, *, retry=False):
        self.source, self.intent, self.retry = source, intent, retry

    def __getattr__(self, name):
        return getattr(self.source, name)

    async def _candidate(self, selection):
        from services.skills.runtime import SkillBindingError
        rows = await asyncio.to_thread(self.source.repository.pinned_candidates,
                                       selection.skill_id, selection.revision)
        package_id = self.intent.package_ids.get(selection.skill_id)
        if package_id is not None:
            rows = [row for row in rows if row.package_id == package_id]
        elif len(rows) > 1:
            raise SkillBindingError('原 Skill 的来源存在歧义，请重新选择后发送新任务。')
        context = await self.source._resolution_context(rows)
        from services.skills.resolver import SkillResolver
        rows = SkillResolver().select(context, rows)
        if len(rows) != 1:
            raise SkillBindingError('原任务的 Skill 已停用、版本失效或无权限，请重新选择后发送新任务。')
        return rows[0]

    async def session_bindings(self):
        return [await self._candidate(s) for s in self.intent.session_skills]

    async def discover(self):
        if self.retry:
            return [await self._candidate(self.intent.selected_skill)] if self.intent.selected_skill else []
        return await self.source.discover()
