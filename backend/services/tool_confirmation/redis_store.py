"""Fail-closed Redis adapter for Tool Confirmation V3."""

from __future__ import annotations

import hashlib
import json
from core.redis import RedisClient
from services.tool_confirmation.scripts import (
    CLAIM_SCRIPT, CONSUME_SCRIPT, CREATE_SCRIPT, EXPIRE_SCRIPT, READ_SCRIPT,
)
from services.tool_confirmation.types import ConfirmationBinding

CONFIRM_SECONDS = 60
CLAIM_SECONDS = 15
TERMINAL_SECONDS = 120
KEY_PREFIX = "ws:tool-confirm:{tool-confirm}"


def hash_waiter_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def identity_digest(binding: ConfirmationBinding) -> str:
    value = json.dumps([
        binding.action_id, binding.interaction_id,
        binding.interaction_version, binding.task_id,
        binding.tool_call_id, binding.tool_name,
        binding.user_id, binding.org_id,
    ], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def confirmation_keys(
    binding: ConfirmationBinding, confirmation_id: str,
) -> tuple[str, str, str]:
    digest = identity_digest(binding)
    return (
        f"{KEY_PREFIX}:identity:{digest}",
        f"{KEY_PREFIX}:challenge:{confirmation_id}",
        f"{KEY_PREFIX}:signal:{confirmation_id}",
    )


class ToolConfirmationRedisStore:
    async def find(
        self, binding: ConfirmationBinding,
    ) -> tuple[str, dict[str, str]] | None:
        client = await RedisClient.get_client()
        digest = identity_digest(binding)
        identity_key = f"{KEY_PREFIX}:identity:{digest}"
        confirmation_id = await client.get(identity_key)
        if not confirmation_id:
            return None
        challenge_key = f"{KEY_PREFIX}:challenge:{confirmation_id}"
        record = await client.hgetall(challenge_key)
        if not isinstance(record, dict) or not record:
            raise RuntimeError("MALFORMED_STATE")
        return str(confirmation_id), {
            str(key): str(value) for key, value in record.items()
        }

    async def create(
        self, confirmation_id: str, binding: ConfirmationBinding,
        waiter_hash: str,
    ) -> str:
        client = await RedisClient.get_client()
        keys = confirmation_keys(binding, confirmation_id)
        result = await client.eval(
            CREATE_SCRIPT, 2, keys[0], keys[1], confirmation_id,
            binding.action_id, binding.interaction_id,
            binding.interaction_version, binding.task_id,
            binding.tool_call_id, binding.tool_name, binding.arguments_hash,
            binding.user_id, binding.org_id, binding.expires_at.isoformat(),
            waiter_hash, CONFIRM_SECONDS, TERMINAL_SECONDS,
        )
        return str(result)

    async def consume(
        self, confirmation_id: str, binding: ConfirmationBinding,
        user_id: str, org_id: str, approved: bool,
    ) -> str:
        client = await RedisClient.get_client()
        keys = confirmation_keys(binding, confirmation_id)
        result = await client.eval(
            CONSUME_SCRIPT, 3, *keys, confirmation_id, user_id, org_id,
            "1" if approved else "0", TERMINAL_SECONDS, CLAIM_SECONDS,
        )
        return str(result)

    async def expire(self, confirmation_id: str, binding: ConfirmationBinding) -> str:
        client = await RedisClient.get_client()
        keys = confirmation_keys(binding, confirmation_id)
        return str(await client.eval(EXPIRE_SCRIPT, 3, *keys, confirmation_id, TERMINAL_SECONDS))

    async def claim(
        self, confirmation_id: str, binding: ConfirmationBinding,
        waiter_hash: str,
    ) -> str:
        client = await RedisClient.get_client()
        keys = confirmation_keys(binding, confirmation_id)
        result = await client.eval(
            CLAIM_SCRIPT, 2, keys[0], keys[1], confirmation_id,
            waiter_hash, TERMINAL_SECONDS,
        )
        return str(result)

    async def read(self, confirmation_id: str, binding: ConfirmationBinding) -> dict[str, str]:
        client = await RedisClient.get_client()
        keys = confirmation_keys(binding, confirmation_id)
        raw = await client.eval(READ_SCRIPT, 2, keys[0], keys[1], confirmation_id)
        if (
            not isinstance(raw, list) or not raw
            or raw[0] in {"NOT_FOUND", "MALFORMED_STATE"}
        ):
            raise RuntimeError(str(raw[0] if raw else "MALFORMED_STATE"))
        return {str(raw[i]): str(raw[i + 1]) for i in range(0, len(raw), 2)}

    async def wait_signal(
        self, confirmation_id: str, binding: ConfirmationBinding,
        timeout: int = CONFIRM_SECONDS,
    ) -> None:
        client = await RedisClient.get_client()
        key = confirmation_keys(binding, confirmation_id)[2]
        await client.blpop(key, timeout=timeout)
