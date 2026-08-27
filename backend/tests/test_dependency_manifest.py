"""发布依赖契约：确保干净虚拟环境具备运行时直接导入的包。"""

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    (
        "aiofiles",
        "alibabacloud_tea_openapi",
        "dotenv",
        "multipart",
        "numpy",
        "openai",
        "postgrest",
        "psycopg2",
        "requests",
        "supabase",
    ),
)
def test_direct_runtime_dependencies_are_importable(module_name: str):
    """requirements.txt 必须覆盖代码直接使用的运行时依赖。"""

    assert importlib.util.find_spec(module_name) is not None
    importlib.import_module(module_name)
