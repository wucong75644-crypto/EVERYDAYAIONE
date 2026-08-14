"""Composition helpers for the chat-to-Runtime Action submission path."""

from services.agent.runtime.application.chat_action_bridge import (
    RuntimeChatActionPersistenceExecutor,
)
from services.agent.runtime.infrastructure.postgres.action_repository import (
    PostgresActionRepository,
)
from services.agent.runtime.executors.registry import ExecutorRegistry


def build_production_chat_action_executor(
    *, database, registry: ExecutorRegistry,
    policy_revision: str = "runtime-chat-v1",
):
    """Use the existing scoped repository and Catalog registry."""
    return RuntimeChatActionPersistenceExecutor(
        submission=PostgresActionRepository(database),
        registry=registry,
        policy_revision=policy_revision,
    )


__all__ = ["build_production_chat_action_executor"]
