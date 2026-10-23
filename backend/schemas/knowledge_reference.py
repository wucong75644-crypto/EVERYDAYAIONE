"""Typed user intent. Never accepts authorization or snapshot facts."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict

class KnowledgeRoot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['object', 'container']
    id: UUID

class KnowledgeReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    root: KnowledgeRoot
    policy: Literal['latest_saved'] = 'latest_saved'
