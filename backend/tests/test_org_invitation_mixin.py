"""Boundary tests for organization invitations."""

import pytest

from core.exceptions import ConflictError, NotFoundError, ValidationError
from services.org.org_service import OrgService
from tests.test_org_service import FakeDB


def _service_with_admin() -> tuple[FakeDB, OrgService]:
    db = FakeDB()
    db.set_table("organizations", {"id": "org-1", "status": "active"})
    db.set_table("org_members", {"role": "admin", "status": "active"})
    return db, OrgService(db)


def _pending_invitation() -> dict:
    return {
        "id": "inv-1",
        "org_id": "org-1",
        "phone": "13800138000",
        "role": "member",
        "status": "pending",
        "invited_by": "admin-1",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }


def test_create_rejects_existing_member() -> None:
    db, service = _service_with_admin()
    db.set_table("users", {"id": "user-1"})
    db.set_table("org_members", {"user_id": "user-1"})

    with pytest.raises(ConflictError, match="已是成员"):
        service.create_invitation(
            "org-1", "admin-1", "13800138000",
        )


def test_create_rejects_existing_pending_invitation() -> None:
    db, service = _service_with_admin()
    db.set_table("users", [])
    db.set_table("org_invitations", {"id": "inv-existing"})

    with pytest.raises(ConflictError, match="已有待处理"):
        service.create_invitation(
            "org-1", "admin-1", "13800138000",
        )


def test_accept_rejects_missing_or_used_invitation() -> None:
    service = OrgService(FakeDB())
    with pytest.raises(NotFoundError):
        service.accept_invitation("missing", "user-1")

    db = FakeDB()
    invitation = _pending_invitation()
    invitation["status"] = "accepted"
    db.set_table("org_invitations", invitation)
    with pytest.raises(NotFoundError):
        OrgService(db).accept_invitation("used", "user-1")


def test_accept_rejects_missing_user() -> None:
    db = FakeDB()
    db.set_table("org_invitations", _pending_invitation())
    db.set_table("users", [])

    with pytest.raises(ValidationError, match="手机号不匹配"):
        OrgService(db).accept_invitation("token", "missing-user")


def test_accept_marks_existing_member_invitation_accepted() -> None:
    db = FakeDB()
    db.set_table("org_invitations", _pending_invitation())
    db.set_table("users", {"phone": "13800138000"})
    db.set_table("organizations", {
        "id": "org-1", "name": "Org", "status": "active",
    })
    db.set_table("org_members", {"user_id": "user-1"})
    db.set_table("org_invitations", {"id": "inv-1"})

    with pytest.raises(ConflictError, match="已是该企业成员"):
        OrgService(db).accept_invitation("token", "user-1")


def test_accept_rejects_full_organization() -> None:
    db = FakeDB()
    db.set_table("org_invitations", _pending_invitation())
    db.set_table("users", {"phone": "13800138000"})
    db.set_table("organizations", {
        "id": "org-1", "name": "Org", "status": "active",
        "max_members": 1,
    })
    db.set_table("org_members", [])
    db.set_table("org_members", [], count=1)

    with pytest.raises(ValidationError, match="成员数已达上限"):
        OrgService(db).accept_invitation("token", "user-1")


def test_accept_adds_member_and_marks_invitation_accepted() -> None:
    db = FakeDB()
    db.set_table("org_invitations", _pending_invitation())
    db.set_table("users", {"phone": "13800138000"})
    db.set_table("organizations", {
        "id": "org-1", "name": "Org", "status": "active",
        "max_members": 2,
    })
    db.set_table("org_members", [])
    db.set_table("org_members", [], count=0)
    db.set_table("org_members", [])
    db.set_table("org_invitations", {"id": "inv-1"})

    result = OrgService(db).accept_invitation("token", "user-1")

    assert result == {
        "org_id": "org-1", "role": "member", "org_name": "Org",
    }
