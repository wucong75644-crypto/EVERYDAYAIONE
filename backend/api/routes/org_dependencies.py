"""Shared organization route service dependencies."""

from api.deps import PlatformDB, ScopedDB
from services.org.org_service import OrgService


def get_org_service(db: ScopedDB) -> OrgService:
    return OrgService(db)


def get_platform_org_service(db: PlatformDB) -> OrgService:
    return OrgService(db)
