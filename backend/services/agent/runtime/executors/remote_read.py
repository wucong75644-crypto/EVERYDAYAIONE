"""Remote ERP/search/social read Executor family."""

from services.agent.runtime.executors.specialist_registry import REMOTE_READ_TOOLS
from services.agent.runtime.executors.family_executors import RemoteReadExecutor

__all__ = ["REMOTE_READ_TOOLS", "RemoteReadExecutor"]
