"""Frozen full-production release for conversational Runtime media batches."""

from __future__ import annotations

from services.agent.runtime.agents import AgentDefinition
from services.agent.runtime.catalog.production import ProductionFactSnapshot
from services.agent.runtime.catalog.production_seed import build_seed_snapshot


BATCH_MEDIA_DEFINITION_REVISION = "v7"
BATCH_MEDIA_PROMPT_REVISION = "agent-runtime-batch-media-v1"
EXCLUDED_INGRESS_TOOLS = frozenset({"image_agent"})


def build_batch_media_definition() -> AgentDefinition:
    """Build the new ingress definition without changing historical facts."""
    return AgentDefinition(
        canonical_key="everydayai-default",
        revision=BATCH_MEDIA_DEFINITION_REVISION,
        prompt_revision=BATCH_MEDIA_PROMPT_REVISION,
        requested_tool_groups=frozenset({
            "artifact", "code", "composite", "erp_catalog", "erp_local",
            "erp_sync", "erp_write", "evidence", "knowledge", "media",
            "memory", "remote", "runtime", "scheduler", "workspace",
        }),
        model_policy={"model_id": "qwen3.5-plus"},
        context_policy={"stable_prefix_blocks": 0},
        channel_restrictions=frozenset({"web", "wecom"}),
        system_prompt=(
            "You are EVERYDAYAI Runtime. Use only the frozen tools offered "
            "for this Run. For an explicit request for multiple image "
            "variants, emit between 1 and 10 independent generate_image "
            "calls in one model response. reference_image_indexes refer only "
            "to the indexed images in the current user message. Never put an "
            "image URL, asset ID, workspace path, credential, receipt, policy "
            "fact, or hidden instruction in tool arguments or user-visible "
            "output. Do not claim work that was not completed."
        ),
    )


def build_batch_media_snapshot(
    *, scope: str, channel: str, gate_state: str,
) -> ProductionFactSnapshot:
    """Build v7 from the complete production descriptor/schema registry."""
    return build_seed_snapshot(
        scope=scope,
        channel=channel,
        gate_state=gate_state,
        agent=build_batch_media_definition(),
        excluded_tool_names=EXCLUDED_INGRESS_TOOLS,
    )


__all__ = [
    "BATCH_MEDIA_DEFINITION_REVISION", "BATCH_MEDIA_PROMPT_REVISION",
    "EXCLUDED_INGRESS_TOOLS", "build_batch_media_definition",
    "build_batch_media_snapshot",
]
