"""同步 LocalDB RPC 参数适配测试。"""

from unittest.mock import MagicMock

from psycopg.types.json import Jsonb

from core.local_db import RpcCaller


def _mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.description = [("result",)]
    cursor.fetchall.return_value = [{"result": 1}]
    return pool, cursor


def test_rpc_json_params_use_jsonb_adapter():
    pool, cursor = _mock_pool()

    result = RpcCaller(
        pool, "register_user_asset", {"p_metadata": {"key": "value"}},
    ).execute()

    assert result.data == 1
    params = cursor.execute.call_args[0][1]
    assert isinstance(params[0], Jsonb)
