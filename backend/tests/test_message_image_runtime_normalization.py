"""Runtime image requests use provider-canonical parameter values."""

from datetime import datetime, timezone

from api.routes.message_image_preparation import _task_payloads
from schemas.message import GenerateRequest, GenerationType, TextPart


class _Handler:
    @staticmethod
    def _extract_text_content(content):
        return content[0].text

    @staticmethod
    def _serialize_params(params):
        return dict(params)


def test_runtime_image_task_normalizes_resolution_before_ingress():
    body = GenerateRequest(
        content=[TextPart(text="cat")], generation_type=GenerationType.IMAGE,
        model="image-model", params={"resolution": "1024x1024"},
        client_request_id="client-request", client_task_id="client-task",
        assistant_message_id="00000000-0000-0000-0000-000000000002",
    )

    payload = _task_payloads(
        handler=_Handler(), body=body,
        settings={
            "model_id": "image-model", "aspect_ratio": "1:1",
            "resolution": "1K", "num_images": 1,
        },
        task_ids=("00000000-0000-0000-0000-000000000003",),
        batch_id="00000000-0000-0000-0000-000000000004",
        conversation_id="00000000-0000-0000-0000-000000000005",
        user_id="00000000-0000-0000-0000-000000000006",
        org_id="00000000-0000-0000-0000-000000000007",
        placeholder_at=datetime.now(timezone.utc),
    )[0]

    assert payload["request_params"]["resolution"] == "1K"
