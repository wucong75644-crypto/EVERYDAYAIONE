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

class ObjectMutation(StrictModel):
    object_id: UUID
    expected_revision: int = Field(ge=1)

class DocumentPayload(ObjectMutation):
    body: str = Field(max_length=200000)

class TitlePayload(ObjectMutation):
    title: Title

class ReparentPayload(ObjectMutation):
    parent_id: UUID | None

class MoveNode(StrictModel):
    node_id: UUID
    expected_revision: int = Field(ge=1)
    position: Position

class MovePayload(StrictModel):
    nodes: list[MoveNode] = Field(min_length=1, max_length=100)

class ConnectPayload(StrictModel):
    source_object_id: UUID
    target_object_id: UUID
    relation_type: Literal['reference', 'related', 'dependency']

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
                         ReparentCommand, MoveCommand, ConnectCommand, MemberCommand], Field(discriminator='operation')]
