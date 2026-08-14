"""现有 Provider 调用链的 ModelPort 基础设施适配。"""

from services.agent.runtime.infrastructure.model.adapter import (
    ExistingProviderModelAdapter,
)
from services.agent.runtime.infrastructure.model.configured_adapter import (
    RuntimeConfiguredAdapterFactory,
)
from services.agent.runtime.infrastructure.model.projection import (
    compute_request_hash,
    resolve_model_revision,
)

__all__ = [
    "ExistingProviderModelAdapter",
    "RuntimeConfiguredAdapterFactory",
    "compute_request_hash",
    "resolve_model_revision",
]
