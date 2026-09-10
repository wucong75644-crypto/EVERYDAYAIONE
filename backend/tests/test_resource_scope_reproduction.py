"""Same public calls on deployed bb4449a0 and the repaired candidate."""
import re
from unittest.mock import AsyncMock

import pytest

from services.agent.agent_result import AgentResult
from services.handlers.resource_manifest import ResourceManifest
from tests.test_file_target_execution import fixture


@pytest.mark.parametrize("selector", ["path", "file_id", "resource_ref"])
async def test_production_search_to_analyze_without_repeating_scope(fixture, selector):
    e, _, create = fixture
    e.resource_manifest = ResourceManifest("t", "m", (), "input_message")
    create("downloads/report.csv")
    e._handlers["file_analyze"] = AsyncMock(return_value=AgentResult(summary="analysis-ok"))
    found = str(await e.execute("file_search", {"scope": "workspace", "keyword": "report"}))
    args = {selector: {"path": "downloads/report.csv",
        "file_id": re.search(r"fid_[a-z0-9]{8}", found)[0],
        "resource_ref": re.search(r"fref1_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", found)[0]}[selector]}
    assert str(await e.execute("file_analyze", args)) == "analysis-ok"
    e._handlers["file_analyze"].assert_awaited_once()


async def test_production_root_to_directory_without_repeating_scope(fixture):
    e, _, create = fixture
    e.resource_manifest = ResourceManifest("t", "m", (), "input_message")
    create("downloads/report.csv")
    await e.execute("file_search", {"scope": "workspace"})
    assert "report.csv" in str(await e.execute("file_search", {"path": "downloads/"}))
