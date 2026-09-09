"""Isolated tool catalog foundation; production entrypoints still use legacy code."""

from .context import ToolContext
from .legacy import LegacyAdvertisement, build_legacy_catalog, validate_legacy_coverage
from .registry import ResolvedTools, ToolAccessDecision, ToolAccessPolicy, ToolAdvertisement, ToolRegistry
from .spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec
from .policy import ConfirmationBinding, PlannedToolCall, ToolCall, ToolConfirmation, ToolDecision, ToolPolicy
from .dispatcher import ToolDispatcher, ToolHandler
from .legacy_handler import LegacyToolHandler, build_legacy_handlers
from .result import ToolArtifacts, ToolError, ToolExecutionMetadata, ToolResult
from .execution import ToolExecutionService

__all__ = [
    "Exposure", "ToolAvailability", "ToolSpec", "ToolContext", "ToolRegistry",
    "ToolAccessDecision", "ToolAccessPolicy", "ResolvedTools", "LegacyAdvertisement",
    "ToolAdvertisement", "build_legacy_catalog", "validate_legacy_coverage",
    "ToolPolicyRules", "ToolPolicy", "ToolDecision", "ToolCall", "PlannedToolCall",
    "ConfirmationBinding", "ToolConfirmation",
    "ToolDispatcher", "ToolHandler", "LegacyToolHandler", "build_legacy_handlers",
    "ToolResult", "ToolArtifacts", "ToolError", "ToolExecutionMetadata", "ToolExecutionService",
]
