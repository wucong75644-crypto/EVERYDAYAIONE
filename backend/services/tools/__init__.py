"""Isolated tool catalog foundation; production entrypoints still use legacy code."""

from .context import ToolContext
from .legacy import LegacyAdvertisement, build_legacy_catalog, validate_legacy_coverage
from .registry import ResolvedTools, ToolAccessDecision, ToolAccessPolicy, ToolAdvertisement, ToolRegistry
from .spec import Exposure, ToolAvailability, ToolSpec

__all__ = [
    "Exposure", "ToolAvailability", "ToolSpec", "ToolContext", "ToolRegistry",
    "ToolAccessDecision", "ToolAccessPolicy", "ResolvedTools", "LegacyAdvertisement",
    "ToolAdvertisement", "build_legacy_catalog", "validate_legacy_coverage",
]
