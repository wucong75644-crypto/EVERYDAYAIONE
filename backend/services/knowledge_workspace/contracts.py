"""Strict V1 command boundary. File binding, chat and automation come later."""
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator, model_serializer

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Position(StrictModel):
    x: float = Field(ge=-1000000, le=1000000)
    y: float = Field(ge=-1000000, le=1000000)

class CanvasGroup(StrictModel):
    members: list[UUID] = Field(min_length=1, max_length=1000)
    title: Title = '概要'

class CanvasTask(StrictModel):
    progress: int = Field(default=0, ge=0, le=100)
    priority: int = Field(default=0, ge=0, le=3)
    start: str = Field(default='', max_length=10)
    due: str = Field(default='', max_length=10)
    assignee: str = Field(default='', max_length=100)

    @field_validator('start', 'due')
    @classmethod
    def calendar_date(cls, value):
        if value:
            from datetime import date
            date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def chronological(self):
        if self.start and self.due and self.due < self.start:
            raise ValueError('截止日期不能早于开始日期')
        return self


class CanvasStyle(StrictModel):
    color: str = Field(default='#202938', pattern=r'^#[0-9a-fA-F]{6}$')
    background: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    border: str = Field(default='#a8b3c5', pattern=r'^#[0-9a-fA-F]{6}$')
    font_size: int = Field(default=15, ge=10, le=48)
    bold: bool = False
    italic: bool = False
    shape: Literal['rounded', 'rectangle', 'pill'] = 'rounded'
    font: Literal['system','sans','serif','mono'] = 'system'
    underline: bool = False
    strike: bool = False
    border_width: int = Field(default=1, ge=1, le=6)
    border_style: Literal['solid','dashed','dotted'] = 'solid'

class CanvasImage(StrictModel):
    binding_id: UUID
    revision: int = Field(ge=1)
    width: int = Field(default=220, ge=40, le=240)
    placement: Literal['top','bottom'] = 'top'
    aspect: float = Field(default=1, ge=0.01, le=100)
    alt: str = Field(default='', max_length=200)

class CanvasVideo(StrictModel):
    binding_id: UUID
    revision: int = Field(ge=1)
    title: str = Field(default='', max_length=200)

class CanvasLink(StrictModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default='', max_length=200)
    kind: Literal['auto','video','web'] = 'auto'

    @field_validator('url')
    @classmethod
    def public_web_url(cls, value):
        from urllib.parse import urlsplit
        if any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value:
            raise ValueError('请输入完整的 HTTP 或 HTTPS 网址')
        try:
            url=urlsplit(value)
            if url.scheme not in ('http','https') or not url.hostname or url.username is not None or url.password is not None:
                raise ValueError('请输入完整的 HTTP 或 HTTPS 网址')
            _=url.port
        except ValueError as exc:
            raise ValueError('请输入完整的 HTTP 或 HTTPS 网址') from exc
        return value

TopicStructure = Literal['right','left','balanced','down','up']

class TitleRun(StrictModel):
    text: str = Field(min_length=1, max_length=200)
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    font_size: int | None = Field(default=None, ge=10, le=48)

class TitleFormat(StrictModel):
    runs: list[TitleRun] = Field(default_factory=list, max_length=200)
    align: Literal['left', 'center', 'right'] = 'left'

class TemplateTopic(StrictModel):
    title_format: TitleFormat | None = None
    structure: TopicStructure | None = None
    title: Title
    parent_index: int = Field(ge=-1, le=199)
    position: Position

class TemplatePayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    parent_id: UUID | None = None
    topics: list[TemplateTopic] = Field(min_length=1, max_length=200)
    nodes: list['MoveNode'] = Field(default_factory=list, max_length=10000)
    after_object_id: UUID | None = None
    before_object_id: UUID | None = None

    @model_validator(mode='after')
    def valid_tree(self):
        for index, topic in enumerate(self.topics):
            if index > 0 and topic.title_format is not None:
                raise ValueError('批量创建时仅根主题可指定局部格式')
            if (index==0 and topic.parent_index!=-1) or (index>0 and not 0<=topic.parent_index<index):
                raise ValueError('模板须为单一且无循环的主题树')
        return self

class TemplateCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_template']
    payload: TemplatePayload

class TopicForestPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    parent_id: UUID | None = None
    topics: list[TemplateTopic] = Field(min_length=1, max_length=200)
    nodes: list['MoveNode'] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def forest(self):
        for i, topic in enumerate(self.topics):
            if i > 0 and topic.title_format is not None:
                raise ValueError('批量创建时仅首个主题可指定局部格式')
            if topic.parent_index >= i:
                raise ValueError('父主题必须位于子主题之前')
        return self

class InsertParentPayload(TopicForestPayload):
    object_id: UUID
    expected_revision: int = Field(ge=1)

    @model_validator(mode='after')
    def one_parent(self):
        if len(self.topics) != 1 or self.topics[0].parent_index != -1:
            raise ValueError('一次只能插入一个父主题')
        return self

class TopicForestCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_topics']
    payload: TopicForestPayload

class InsertParentCommand(StrictModel):
    command_id: UUID
    operation: Literal['insert_parent']
    payload: InsertParentPayload

class RelationAnchor(StrictModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def edge(self):
        if self.x not in (0,1) and self.y not in (0,1):
            raise ValueError('端点必须位于节点边缘')
        return self

class RelationControl(StrictModel):
    x: float = Field(ge=-4000, le=4000, allow_inf_nan=False)
    y: float = Field(ge=-4000, le=4000, allow_inf_nan=False)

class RelationAppearance(StrictModel):
    text: str = Field(default='', max_length=200)
    bend: float = Field(default=0, ge=-2000, le=2000, allow_inf_nan=False)
    source_anchor: RelationAnchor | None = None
    target_anchor: RelationAnchor | None = None
    source_control: RelationControl | None = None
    target_control: RelationControl | None = None

    @model_serializer(mode='wrap')
    def compact_geometry(self, handler):
        return {k:v for k,v in handler(self).items() if v is not None}

class RelationAppearancePayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    relation_id: UUID
    appearance: RelationAppearance

class RelationAppearanceCommand(StrictModel):
    command_id: UUID
    operation: Literal['update_relation']
    payload: RelationAppearancePayload

class CanvasBoundary(CanvasGroup):
    id: UUID

class CanvasData(StrictModel):
    boundaries: list[CanvasBoundary] | None = Field(default=None, max_length=100)
    title_format: TitleFormat | None = None
    width: int | None = Field(default=None, ge=80, le=800)
    structure: TopicStructure | None = None
    style: CanvasStyle | None = None
    image: CanvasImage | None = None
    video: CanvasVideo | None = None
    link: CanvasLink | None = None
    note: str = Field(default='', max_length=20000)
    labels: list[Annotated[str, StringConstraints(strip_whitespace=True,min_length=1,max_length=50)]] = Field(default_factory=list,max_length=20)
    task: CanvasTask | None = None
    summary: CanvasGroup | None = None
    boundary: CanvasGroup | None = None

class CanvasDataUpdate(StrictModel):
    object_id: UUID
    expected_layout_revision: int = Field(ge=1)
    canvas_data: CanvasData

class CanvasDataPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    updates: list[CanvasDataUpdate] = Field(min_length=1,max_length=1000)
    nodes: list['MoveNode'] = Field(default_factory=list, max_length=10000)

class SummaryGroup(CanvasGroup):
    position: Position

class SummaryPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    groups: list[SummaryGroup] = Field(min_length=1,max_length=1000)

class SummaryCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_summaries']
    payload: SummaryPayload

class CanvasDataCommand(StrictModel):
    command_id: UUID
    operation: Literal['update_canvas_data']
    payload: CanvasDataPayload

class ProjectPayload(StrictModel):
    title: Title
    position: Position

class CreatePayload(ProjectPayload):
    title_format: TitleFormat | None = None
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
    title_format: TitleFormat | None = None
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

class SnapshotPosition(StrictModel):
    source_object_id: UUID
    position: Position

class BatchPayload(StrictModel):
    snapshot_positions: list[SnapshotPosition] = Field(default_factory=list, max_length=1000)
    nodes: list[MoveNode] = Field(default_factory=list, max_length=10000)
    snapshot_id: UUID | None = None
    nodes: list[MoveNode] | None = Field(default=None,max_length=10000)

    @model_serializer(mode='wrap')
    def optional_layout(self,handler):
        return {k:v for k,v in handler(self).items() if k!='nodes' or v is not None}

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

class RelationTopicPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    source_object_id: UUID
    position: Position
    relation_type: Literal['reference','related','dependency'] = 'related'
    relation_id: UUID | None = None
    endpoint: Literal['source','target'] = 'target'

class RelationTopicCommand(StrictModel):
    command_id: UUID
    operation: Literal['create_relation_topic']
    payload: RelationTopicPayload

class RelationPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    relation_id: UUID

class ReconnectPayload(RelationPayload):
    appearance: RelationAppearance | None = None
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

class MapLevel(StrictModel):
    depth: int = Field(ge=0, le=31)
    style: CanvasStyle

class MapLayout(StrictModel):
    auto_balance: bool = False
    compact: bool = False
    equal_width: bool = False

class MapTheme(StrictModel):
    layout: MapLayout | None = None

    @model_serializer(mode='wrap')
    def optional_layout(self, handler):
        return {k:v for k,v in handler(self).items() if k != 'layout' or v is not None}

    palette: Literal['none','ocean','forest','sunset','mono'] = 'none'
    background: str = Field(default='auto', pattern=r'^(auto|#[0-9a-fA-F]{6})$')
    grid: bool = True
    font: Literal['system','sans','serif','mono'] = 'system'
    font_size: int = Field(default=15, ge=10, le=48)
    levels: list[MapLevel] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def unique_depths(self):
        if len({x.depth for x in self.levels}) != len(self.levels):
            raise ValueError('层级样式不可重复')
        return self

class MapStyleUpdate(StrictModel):
    object_id: UUID
    expected_layout_revision: int = Field(ge=1)
    style: CanvasStyle | None = None

class MapThemePayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    theme: MapTheme
    updates: list[MapStyleUpdate] = Field(default_factory=list, max_length=10000)
    nodes: list[MoveNode] = Field(default_factory=list, max_length=10000)

class MapThemeCommand(StrictModel):
    command_id: UUID
    operation: Literal['update_map_theme']
    payload: MapThemePayload

class DeleteTopicPayload(StrictModel):
    container_id: UUID
    expected_container_revision: int = Field(ge=1)
    object_id: UUID
    expected_revision: int = Field(ge=1)
    nodes: list[MoveNode] = Field(default_factory=list, max_length=10000)

class DeleteTopicCommand(StrictModel):
    command_id: UUID
    operation: Literal['delete_topic']
    payload: DeleteTopicPayload

Command = Annotated[Union[RelationTopicCommand, MapThemeCommand, DeleteTopicCommand, TopicForestCommand, InsertParentCommand, TemplateCommand, RelationAppearanceCommand, SummaryCommand, CanvasDataCommand, CreateCommand, ProjectCommand, DocumentCommand, TitleCommand,
                         ReparentCommand, MoveCommand, BranchCommand, HistoryCommand, BatchCommand, GroupMoveCommand, ConnectCommand, DeleteRelationCommand, ReconnectCommand, MemberCommand], Field(discriminator='operation')]
