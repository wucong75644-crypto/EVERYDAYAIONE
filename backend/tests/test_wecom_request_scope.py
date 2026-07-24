"""企业微信消息级 DatabaseScope 隔离测试。"""

from unittest.mock import MagicMock

from core.org_scoped_db import OrgScopedDB
from services.wecom.wecom_message_service import WecomMessageService


ORG_A = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"
ORG_B = "5fb02c39-7558-467b-a749-a416f354e107"
USER_A = "f566f6cc-3e7a-4383-befe-42c05fbfbff8"
USER_B = "72fbe19e-c790-4e75-9087-cbf78bb243e2"


def _settings(service: WecomMessageService) -> tuple[str, str, str, str]:
    assert isinstance(service.db, OrgScopedDB)
    return service.db._db.scope.settings


def test_request_services_do_not_mutate_shared_service_scope() -> None:
    base_db = MagicMock()
    root = WecomMessageService(base_db)

    request_a = root._for_request(
        actor_user_id=None,
        org_id=ORG_A,
        request_id="message-a",
    )
    request_b = root._for_request(
        actor_user_id=None,
        org_id=ORG_B,
        request_id="message-b",
    )

    assert root.db is base_db
    assert request_a is not request_b
    assert request_a._user_svc is not request_b._user_svc
    assert _settings(request_a) == ("", ORG_A, "runtime", "message-a")
    assert _settings(request_b) == ("", ORG_B, "runtime", "message-b")


def test_user_mapping_promotes_only_current_request_to_full_scope() -> None:
    root = WecomMessageService(MagicMock())
    request_a = root._for_request(
        actor_user_id=None,
        org_id=ORG_A,
        request_id="message-a",
    )
    request_b = root._for_request(
        actor_user_id=None,
        org_id=ORG_B,
        request_id="message-b",
    )

    request_a._bind_request_db(
        actor_user_id=USER_A,
        org_id=ORG_A,
        request_id="message-a",
    )
    request_b._bind_request_db(
        actor_user_id=USER_B,
        org_id=ORG_B,
        request_id="message-b",
    )

    assert _settings(request_a) == (USER_A, ORG_A, "runtime", "message-a")
    assert _settings(request_b) == (USER_B, ORG_B, "runtime", "message-b")
    assert request_a._user_svc.db is request_a.db
    assert request_b._conv_svc.db is request_b.db
