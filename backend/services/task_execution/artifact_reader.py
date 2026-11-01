"""Fenced, closed-history reads of real saved text; no model scope arguments."""
from __future__ import annotations

from uuid import UUID
from pydantic import ValidationError

from services.conversation_state import ConversationStopRequested
from .contracts import Artifact, TaskError
from .database_scope import task_database


class TaskArtifactReader:
    def __init__(self, db, *, task_id, execution_token):
        self.db, self.task_id, self.execution_token = db, str(task_id), str(execution_token)

    async def native_result(self,state,*,tool_call_id=None,artifact_id=None):
        try:
            response=await task_database(self.db,state).rpc('read_agent_task_native_results',{
                'p_task_id':self.task_id,'p_execution_token':self.execution_token,'p_logical_task_id':str(state.root_task_id),
                'p_expected_revision':state.revision,'p_tool_call_id':tool_call_id,'p_artifact_id':artifact_id}).execute()
        except Exception:raise TaskError('TASK_NATIVE_RESULT_READ_FAILED') from None
        value=response.data if response else None
        if not isinstance(value,dict):raise TaskError('TASK_NATIVE_RESULT_INVALID')
        if value.get('outcome') in {'ownership_lost','lease_expired'}:raise ConversationStopRequested('ownership_lost')
        if value.get('outcome')=='stale':raise TaskError('TASK_REVISION_STALE')
        if value.get('outcome')!='found':raise TaskError('TASK_NATIVE_RESULT_NOT_FOUND')
        return value

    async def terminal_images(self,state):
        response=await task_database(self.db,state).rpc('read_agent_task_terminal_images',{
            'p_task_id':self.task_id,'p_execution_token':self.execution_token,'p_logical_task_id':str(state.root_task_id),
            'p_expected_revision':state.revision}).execute()
        data=response.data if response else None
        if not isinstance(data,dict) or data.get('outcome')!='found':raise TaskError('TASK_IMAGE_RESULTS_READ_UNCONFIRMED')
        return data

    async def reusable_images(self,state,*,source_task_id=None):
        response=await task_database(self.db,state).rpc('read_agent_task_reusable_images',{
            'p_task_id':self.task_id,'p_execution_token':self.execution_token,'p_logical_task_id':str(state.root_task_id),
            'p_expected_revision':state.revision,'p_source_task_id':str(source_task_id) if source_task_id else None}).execute()
        data=response.data if response else None
        if not isinstance(data,dict) or data.get('outcome')!='found':raise TaskError('TASK_IMAGE_REUSE_READ_UNCONFIRMED')
        return data['images']

    async def list_legacy(self, state, *, limit):
        try:
            response = await task_database(self.db,state).rpc('read_agent_task_legacy_source',{
                'p_task_id':self.task_id,'p_execution_token':self.execution_token,'p_logical_task_id':str(state.root_task_id),
                'p_expected_revision':state.revision,'p_kind':'discover','p_source_id':None,'p_source_revision':None}).execute()
        except Exception:
            raise TaskError('TASK_LEGACY_SOURCE_READ_FAILED') from None
        value=response.data if response else None
        if not isinstance(value,dict):
            raise TaskError('TASK_LEGACY_SOURCE_INVALID')
        if value.get('outcome') in {'ownership_lost','lease_expired'}:
            raise ConversationStopRequested('ownership_lost')
        if value.get('outcome')=='stale':
            raise TaskError('TASK_REVISION_STALE')
        if value.get('outcome')!='found' or not isinstance(value.get('sources'),list):
            raise TaskError('TASK_LEGACY_SOURCE_INVALID')
        return value['sources'][:limit]

    async def _read(self, state, *, source_id=None, artifact_id=None, limit=10):
        try:
            response = await task_database(self.db, state).rpc('read_agent_task_text_artifacts', {
                'p_task_id': self.task_id, 'p_execution_token': self.execution_token,
                'p_logical_task_id': str(state.root_task_id), 'p_expected_revision': state.revision,
                'p_source_logical_task_id': str(source_id) if source_id else None,
                'p_artifact_id': artifact_id, 'p_limit': limit,
            }).execute()
        except Exception:
            raise TaskError('TASK_ARTIFACT_READ_FAILED') from None
        data = response.data if response else None
        if not isinstance(data, dict):
            raise TaskError('TASK_ARTIFACT_RESULT_INVALID')
        if data.get('outcome') in {'ownership_lost', 'lease_expired'}:
            raise ConversationStopRequested('ownership_lost')
        if data.get('outcome') == 'stale':
            raise TaskError('TASK_REVISION_STALE')
        if data.get('outcome') != 'found' or not isinstance(data.get('artifacts'), list):
            raise TaskError('TASK_ARTIFACT_RESULT_INVALID')
        return data['artifacts']

    async def list_text(self, state, *, limit):
        rows = await self._read(state, limit=limit)
        result = []
        try:
            for row in rows:
                source = UUID(row['source_logical_task_id'])
                artifact = Artifact.model_validate(row['artifact'])
                if artifact.kind != 'text':
                    raise TaskError('TASK_ARTIFACT_RESULT_INVALID')
                result.append({'source_logical_task_id': str(source), 'artifact_id': artifact.id,
                    'source_goal': str(row.get('source_goal') or '')[:500],
                    'sha256': artifact.sha256, 'reference': artifact.reference})
        except (ValidationError, ValueError, KeyError, TypeError):
            raise TaskError('TASK_ARTIFACT_RESULT_INVALID') from None
        return result

    async def load_text(self, state, *, source_logical_task_id, artifact_id):
        rows = await self._read(state, source_id=source_logical_task_id, artifact_id=artifact_id, limit=1)
        if not rows:
            raise TaskError('TASK_ARTIFACT_NOT_FOUND')
        try:
            row = rows[0]
            artifact = Artifact.model_validate(row['artifact'])
            if (UUID(row['source_logical_task_id']) != source_logical_task_id or artifact.id != artifact_id
                    or artifact.kind != 'text' or not artifact.inline_text or not artifact.inline_text.strip()):
                raise TaskError('TASK_ARTIFACT_RESULT_INVALID')
        except (ValidationError, ValueError, KeyError, TypeError):
            raise TaskError('TASK_ARTIFACT_RESULT_INVALID') from None
        return artifact
