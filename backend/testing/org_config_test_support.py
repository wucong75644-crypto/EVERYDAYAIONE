"""Reusable query builders for organization configuration tests."""

from unittest.mock import MagicMock

class FakeQueryBuilder:
    """模拟 Supabase query builder 链式调用"""

    def __init__(self, data=None, count=None):
        # 统一存为 list
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data if data is not None else []
        self._count = count
        self._is_single = False

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        self._data = [{"id": "new-id", **data}]
        return self

    def upsert(self, data, on_conflict=""):
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data
        return self

    def update(self, data):
        if self._data:
            self._data = [{**self._data[0], **data}]
        return self

    def delete(self):
        return self

    def eq(self, col, val):
        return self

    def neq(self, col, val):
        return self

    def single(self):
        self._is_single = True
        return self

    def maybe_single(self):
        self._is_single = True
        return self

    def limit(self, n):
        return self

    def order(self, col):
        return self

    def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        result.count = self._count
        return result


class FakeDB:
    def __init__(self):
        self._tables: dict[str, list] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self._rpc_results: list = []

    def set_table(self, name: str, data):
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(FakeQueryBuilder(data))

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return FakeQueryBuilder()

    def set_rpc(self, data):
        self._rpc_results.append(data)

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        data = self._rpc_results.pop(0) if self._rpc_results else None
        caller = MagicMock()
        caller.execute.return_value = MagicMock(data=data)
        return caller


class AsyncFakeQueryBuilder:
    """异步版 FakeQueryBuilder — execute 返回 awaitable"""

    def __init__(self, data=None):
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data if data is not None else []
        self._is_single = False

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def single(self):
        self._is_single = True
        return self
    def maybe_single(self):
        self._is_single = True
        return self

    async def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        return result


class AsyncFakeDB:
    """异步版 FakeDB"""

    def __init__(self):
        self._tables: dict[str, list] = {}

    def set_table(self, name: str, data):
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(AsyncFakeQueryBuilder(data))

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return AsyncFakeQueryBuilder()
