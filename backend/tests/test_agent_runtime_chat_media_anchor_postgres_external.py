from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION,
    ORG,
    USER,
    _connect,
    _settings,
    database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
    _seed_batch,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def test_chat_media_action_persists_input_and_output_anchors(database: str) -> None:
    _prepare_legacy_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            (ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql")
            .read_text(encoding="utf-8")
        )
        connection.execute(
            (ROOT / "migrations/230_13_agent_runtime_chat_media_anchor.sql")
            .read_text(encoding="utf-8")
        )

    chat_task, input_message, output_message = _chat_message_ids(
        database, batch.output_id,
    )
    idempotency_key = f"chat-media-anchor:{chat_task}"
    with _connect(database, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        result = connection.execute(
            """
            SELECT submit_agent_runtime_chat_action_v1(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                CONVERSATION,
                ORG,
                USER,
                str(chat_task),
                str(output_message),
                "chat-media-call",
                1,
                "generate_image",
                Jsonb({"prompt": "a red cup", "reference_image_indexes": [0]}),
                "gpt-image-2-image-to-image",
                "runtime",
                "chat-runtime-v1",
                "runtime-image-v13",
                "runtime-media-v1",
                "runtime_media_generation:generate_image",
                1,
                Jsonb({"source": "chat"}),
                Jsonb({}),
                idempotency_key,
            ),
        ).fetchone()[0]
        assert result["outcome"] == "created"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        payload = connection.execute(
            "SELECT payload FROM agent_session_commands WHERE idempotency_key=%s",
            (idempotency_key,),
        ).fetchone()[0]
    assert payload["input_message_id"] == str(input_message)
    assert payload["output_message_id"] == str(output_message)


def _chat_message_ids(database: str, output_id) -> tuple[object, object, object]:
    with psycopg.connect(database) as connection:
        row = connection.execute(
            "SELECT id, input_message_id, assistant_message_id FROM tasks "
            "WHERE assistant_message_id=%s AND type='chat'",
            (output_id,),
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]
