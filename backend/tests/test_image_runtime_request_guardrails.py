"""Ordinary Runtime image count and ownership readback guardrails."""

from types import SimpleNamespace

import pytest

from api.routes.message_media_failure import read_prepared_media_ownership
from services.handlers.image_request_settings import resolve_image_generation_settings


@pytest.fixture(autouse=True)
def _stub_image_cost(monkeypatch):
    monkeypatch.setattr(
        "services.handlers.image_request_settings.kie_models.calculate_image_cost",
        lambda **kwargs: {"user_credits": 5 * kwargs["image_count"]},
    )


def _runtime_settings(params):
    return resolve_image_generation_settings(
        params=params, has_image_urls=False, max_images=10,
        batch_prompt_limit=10, strict_count=True,
    )


@pytest.mark.parametrize("count", (1, 4, 10))
def test_runtime_image_count_preserves_supported_values(count):
    settings = _runtime_settings({"model": "image-model", "num_images": count})

    assert settings["num_images"] == count
    assert settings["total_credits"] == 5 * count


def test_runtime_image_count_rejects_eleven():
    with pytest.raises(ValueError, match="IMAGE_COUNT_INVALID"):
        _runtime_settings({"model": "image-model", "num_images": 11})


@pytest.mark.parametrize("count", (10, 11))
def test_runtime_batch_prompts_enforce_ten_item_limit(count):
    params = {
        "model": "image-model",
        "_batch_prompts": [{"prompt": f"image-{index}"} for index in range(count)],
    }

    if count == 11:
        with pytest.raises(ValueError, match="IMAGE_BATCH_PROMPTS_COUNT_INVALID"):
            _runtime_settings(params)
    else:
        assert _runtime_settings(params)["num_images"] == 10


def test_legacy_ecom_count_keeps_existing_four_image_clamp():
    settings = resolve_image_generation_settings(
        params={"model": "image-model", "num_images": 10},
        has_image_urls=False,
    )

    assert settings["num_images"] == 4


def test_regenerate_single_stays_one_image():
    settings = _runtime_settings({
        "model": "image-model", "operation": "regenerate_single",
        "num_images": 10,
    })

    assert settings["num_images"] == 1


class _TasksQuery:
    def __init__(self, rows):
        self.rows = rows
        self.requested_ids = []

    def select(self, fields):
        return self

    def in_(self, field, values):
        assert field == "id"
        self.requested_ids = values
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _TasksDatabase:
    def __init__(self, rows):
        self.query = _TasksQuery(rows)

    def table(self, name):
        assert name == "tasks"
        return self.query


def _task(task_id, *, runtime=False, status="preparing", external=None):
    context = {"channel": "web"}
    if runtime:
        context.update({
            "runtime": True, "runtime_owner": "action_loop",
            "runtime_action_id": f"action-{task_id}",
        })
    return {
        "id": task_id, "status": status, "external_task_id": external,
        "credit_transaction_id": None, "delivery_context": context,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected"),
    (
        ([_task("task-1"), _task("task-2")], "none"),
        ([_task("task-1", runtime=True), _task("task-2", runtime=True)], "full"),
        ([_task("task-1", runtime=True), _task("task-2")], "partial"),
        ([_task("task-1", status="pending"), _task("task-2")], "unknown"),
    ),
)
async def test_task_readback_classifies_ownership_conservatively(rows, expected):
    database = _TasksDatabase(rows)

    assert await read_prepared_media_ownership(
        database, ("task-1", "task-2"),
    ) == expected


@pytest.mark.asyncio
async def test_task_readback_failure_is_unknown():
    assert await read_prepared_media_ownership(
        _TasksDatabase([_task("task-1")]), ("task-1", "task-2"),
    ) == "unknown"
