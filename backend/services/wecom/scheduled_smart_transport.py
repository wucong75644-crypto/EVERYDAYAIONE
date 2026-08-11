"""Tenant-exact Smart Robot transport resolution for Scheduled Runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    SmartRobotProactiveTransportPort,
    SmartRobotReadbackTransportPort,
)


class ScheduledSmartTransportResolver:
    """Resolve exact-org WS dispatch or cache-readback capabilities."""

    def __init__(
        self,
        get_ws_client: Callable[[str], object | None],
    ) -> None:
        self._get_ws_client = get_ws_client

    async def resolve_smart_transport(
        self, org_id: str,
    ) -> SmartRobotProactiveTransportPort | None:
        if not _canonical_uuid(org_id):
            return None
        client = self._get_ws_client(org_id)
        if (
            getattr(client, "org_id", None) != org_id
            or getattr(client, "is_connected", None) is not True
            or not callable(getattr(client, "send_proactive_typed", None))
        ):
            return None
        return cast(SmartRobotProactiveTransportPort, client)

    async def resolve_smart_readback(
        self, org_id: str,
    ) -> SmartRobotReadbackTransportPort | None:
        if not _canonical_uuid(org_id):
            return None
        client = self._get_ws_client(org_id)
        if (
            getattr(client, "org_id", None) != org_id
            or not callable(getattr(client, "lookup_outbound_result", None))
        ):
            return None
        return cast(SmartRobotReadbackTransportPort, client)


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False
