"""Strict V1 command boundary. File binding, chat and automation come later."""
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Position(StrictModel):
    x: float = Field(ge=-1000000, le=1000000)
    y: float = Field(ge=-1000000, le=1000000)

class ProjectPayload(StrictModel):
    title: Title
    position: Position

class CreatePayload(ProjectPayload):
    container_id: UUID
    parent_id: UUID | None = None
    kind: Literal['topic', 'document', 'rule']
    body: str = Field(default='', max_length=200000)
    after_object_id: UUID | None = None
    before_object_id: UUID | None = None
    expected_container_revision: int | None = Field(default=None, ge=1)
    nodes: list['MoveNode'] = Field(default_factory=list, max_length=10000)

class ObjectMutation(StrictModel):
    object_id: UUID
    expected_revision: int = Field(ge=1)

class DocumentPayload(ObjectMutation):
    body: str = Field(max_length=200000)

class TitlePayload(ObjectMutation):
    title: Title

    expected_container_revision: int | None = Field(default=None, ge=1)
    nodes: list['MoveNode'] | None = Field(default=None, max_length=10000)

class ReparentPayload(ObjectMutation):
    parent_id: UUID | None

class MoveNode(StrictModel):
    node_id: UUID
    expected_revision: int = Field(ge=1)
    position: Position

class MovePayload(StrictModel):
    nodes: list[MoveNode] = Field(min_length=1, max_length=100)

class BranchPayload(ObjectMutation):
    expected_container_revision: int = Field(ge=1)
    target_id: UUID | None
    placement: Literal['free', 'child', 'before', 'after']
    nodes: list[MoveNode] = Field(min_length=1, max_length=10000)

class BranchCommand(StrictModel):
    command_id: UUID
    operation: Literal['move_branch']
    payload: BranchPayload

class HistoryPayload(StrictModel):
    history_id: UUID

class HistoryCommand(StrictModel):
    command_id: UUID
    operation: Literal['undo_canvas', 'redo_canvas']
    payload: HistoryPayload

class BranchSelection(StrictModel):
    object_id: UUID
    expected_revision: int = Field(ge=1)

class BatchPayload(StrictModel):
    snapshot_id: UUID | None = None
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    objects: list[BranchSelection] = Field(min_length=1, max_length=1000)
    parent_id: UUID | None = None
    offset: Position = Field(default_factory=lambda: Position(x=40, y=40))

class BranchSnapshotRequest(BatchPayload):
    snapshot_id: UUID

class BatchCommand(StrictModel):
    command_id: UUID
    operation: Literal['copy_branches', 'delete_branches']
    payload: BatchPayload

class GroupMovePayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    objects: list[BranchSelection] = Field(min_length=1, max_length=1000)
    target_id: UUID | None
    placement: Literal['free', 'child', 'before', 'after', 'root']
    nodes: list[MoveNode] = Field(min_length=1, max_length=10000)

class GroupMoveCommand(StrictModel):
    command_id: UUID
    operation: Literal['move_branches']
    payload: GroupMovePayload

class ConnectPayload(StrictModel):
    source_object_id: UUID
    target_object_id: UUID
    relation_type: Literal['reference', 'related', 'dependency']

class RelationPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    relation_id: UUID

class ReconnectPayload(RelationPayload):
    source_object_id: UUID
    target_object_id: UUID

class DeleteRelationCommand(StrictModel):
    command_id: UUID
    operation: Literal['delete_relation']
    payload: RelationPayload

class ReconnectCommand(StrictModel):
    command_id: UUID
    operation: Literal['reconnect_relation']
    payload: ReconnectPayload

class MemberPayload(StrictModel):
    container_id: UUID
    user_id: UUID
    role: Literal['viewer', 'editor', 'manager']
    status: Literal['active', 'removed'] = 'active'

class CreateCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_object']
    payload: CreatePayload

class ProjectCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_project']
    payload: ProjectPayload

class DocumentCommand(StrictModel):
    command_id: UUID
    operation: Literal['save_document']
    payload: DocumentPayload

class TitleCommand(StrictModel):
    command_id: UUID
    operation: Literal['update_title']
    payload: TitlePayload

class ReparentCommand(StrictModel):
    command_id: UUID
    operation: Literal['reparent']
    payload: ReparentPayload

class MoveCommand(StrictModel):
    command_id: UUID
    operation: Literal['move_nodes']
    payload: MovePayload

class ConnectCommand(StrictModel):
    command_id: UUID
    operation: Literal['connect']
    payload: ConnectPayload

class MemberCommand(StrictModel):
    command_id: UUID
    operation: Literal['set_member']
    payload: MemberPayload

Command = Annotated[Union[CreateCommand, ProjectCommand, DocumentCommand, TitleCommand,
                         ReparentCommand, MoveCommand, BranchCommand, HistoryCommand, BatchCommand, GroupMoveCommand, ConnectCommand, DeleteRelationCommand, ReconnectCommand, MemberCommand], Field(discriminator='operation')]
