"""统一上下文运行时的观测与组装边界。"""

from services.agent.runtime.context.budget import (
    ContextBudget,
    derive_context_budget,
    resolve_context_budget,
)
from services.agent.runtime.context.assembler import (
    ContextPlan,
    assemble_history,
)
from services.agent.runtime.context.compaction_guard import (
    acquire_loop_compaction,
    clear_loop_compaction_scope,
    compaction_prefix_fingerprint,
    finish_loop_compaction,
)
from services.agent.runtime.context.receipt import (
    ContextBlockReceipt,
    ContextReceipt,
    build_context_receipt,
)
from services.agent.runtime.context.items import build_turn_context_items
from services.agent.runtime.context.provider_receipt import (
    record_provider_context_receipt,
)
from services.agent.runtime.context.summary_coordination import (
    SummaryCoordination,
    acquire_summary_coordination,
    finish_summary_coordination,
    summary_prefix_fingerprint,
)
from services.agent.runtime.context.telemetry import record_context_event

__all__ = [
    "ContextBudget",
    "ContextBlockReceipt",
    "ContextReceipt",
    "ContextPlan",
    "SummaryCoordination",
    "acquire_loop_compaction",
    "acquire_summary_coordination",
    "build_context_receipt",
    "assemble_history",
    "build_turn_context_items",
    "record_provider_context_receipt",
    "clear_loop_compaction_scope",
    "compaction_prefix_fingerprint",
    "derive_context_budget",
    "finish_loop_compaction",
    "finish_summary_coordination",
    "resolve_context_budget",
    "record_context_event",
    "summary_prefix_fingerprint",
]
