"""Reader export adapter behavior against an isolated PostgreSQL database."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from core.crypto import aes_encrypt, generate_encrypt_key
from services.configuration.legacy_import_source_executor import (
    read_legacy_import_snapshot,
)


pytestmark = pytest.mark.external


def _urls() -> tuple[str, str]:
    admin_url = os.getenv("CONFIG_IMPORT_TEST_ADMIN_URL")
    reader_url = os.getenv("CONFIG_IMPORT_TEST_READER_URL")
    if not admin_url or not reader_url:
        pytest.skip("isolated configuration import reader URLs required")
    return admin_url, reader_url


def test_real_reader_returns_one_consistent_decryptable_export() -> None:
    admin_url, reader_url = _urls()
    org_id = str(uuid4())
    key = generate_encrypt_key()
    marker = f"reader-marker-{uuid4().hex}"
    with psycopg.connect(admin_url) as admin:
        admin.execute(
            "INSERT INTO organizations(id, encrypt_key) VALUES (%s, %s)",
            (org_id, key),
        )
        admin.execute(
            "INSERT INTO org_configs("
            "org_id, config_key, config_value_encrypted"
            ") VALUES (%s, 'ai_google_api_key', %s)",
            (org_id, aes_encrypt(marker, key)),
        )
        admin.commit()
    try:
        with psycopg.connect(reader_url) as reader:
            snapshot = read_legacy_import_snapshot(
                reader,
                global_encrypt_key=None,
            )

        source = next(
            item for item in snapshot.organizations
            if item.org_id == org_id
        )
        assert source.config_values["ai_google_api_key"] == marker
        assert snapshot.preflight_reports[org_id].can_migrate is True
    finally:
        with psycopg.connect(admin_url) as admin:
            admin.execute(
                "DELETE FROM org_configs WHERE org_id = %s",
                (org_id,),
            )
            admin.execute(
                "DELETE FROM organizations WHERE id = %s",
                (org_id,),
            )
