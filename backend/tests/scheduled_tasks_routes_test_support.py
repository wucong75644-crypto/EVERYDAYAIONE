from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI


class FakeQueryBuilder:
    def __init__(self, data=None):
        self._data = data if isinstance(data, list) else ([data] if data else [])
        self._is_single = False
        self._limit = None
        self._is_delete = False
        self._is_update = False

    def select(self, *args, **kwargs):
        return self

    def insert(self, data, **kwargs):
        return self

    def update(self, data, **kwargs):
        self._is_update = True
        return self

    def delete(self):
        self._is_delete = True
        return self

    def eq(self, *args):
        return self

    def in_(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, count):
        self._limit = count
        return self

    def single(self):
        self._is_single = True
        return self

    def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        elif self._is_delete or self._is_update:
            result.data = []
        else:
            result.data = self._data[: self._limit] if self._limit else self._data
        return result


class FakeDB:
    def __init__(self):
        self._tables: dict = {}
        self._rpc_responses: dict = {}

    def add(self, name, data):
        self._tables.setdefault(name, []).append(FakeQueryBuilder(data))

    def add_rpc(self, name, data):
        self._rpc_responses[name] = data

    def table(self, name):
        items = self._tables.get(name, [])
        if items:
            return items.pop(0)
        return FakeQueryBuilder([])

    def rpc(self, name, params=None):
        result = MagicMock()
        result.execute.return_value = MagicMock(
            data=self._rpc_responses.get(name, []),
        )
        return result


def build_app(db, user_id="user_1", org_id="org_1", with_perm=True):
    """Build the scheduled-task route app with scoped DB dependencies."""
    del with_perm
    from api.deps import (
        OrgContext,
        get_current_user_id,
        get_org_context,
        get_request_db,
        get_scoped_db,
    )
    from api.routes.scheduled_tasks import router
    from core.database import get_db
    from main import register_exception_handlers

    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_exception_handlers(app)

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        user_id=user_id,
        org_id=org_id,
        org_role="member",
    )
    app.dependency_overrides[get_scoped_db] = lambda: db
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_request_db] = lambda: db
    return app
