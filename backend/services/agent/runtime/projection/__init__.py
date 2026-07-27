"""Agent Runtime compatibility projection."""

from services.agent.runtime.projection.compatibility import (
    CompatibilityProjection,
    ProjectionAction,
    classify_event,
)

__all__ = [
    "CompatibilityProjection",
    "ProjectionAction",
    "classify_event",
]
