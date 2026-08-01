"""Immutable AgentDefinition registry used by Runtime composition."""

from services.agent.runtime.agents.definition import AgentDefinition
from services.agent.runtime.agents.registry import AgentDefinitionRegistry

__all__ = ["AgentDefinition", "AgentDefinitionRegistry"]
