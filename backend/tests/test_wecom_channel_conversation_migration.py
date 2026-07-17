from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations/128_wecom_channel_conversations.sql"
).read_text()


def test_channel_binding_has_provider_identity_uniqueness() -> None:
    assert "CREATE TABLE IF NOT EXISTS conversation_channel_bindings" in MIGRATION
    assert "UNIQUE (org_id, channel, corp_id, external_chat_id)" in MIGRATION


def test_group_conversation_has_no_personal_owner() -> None:
    assert "scope_type = 'channel' AND user_id IS NULL" in MIGRATION
    assert "chat_type = 'group' AND owner_user_id IS NULL" in MIGRATION


def test_resolver_is_atomic_and_tenant_scoped() -> None:
    assert "CREATE OR REPLACE FUNCTION resolve_wecom_conversation" in MIGRATION
    assert "p_org_id UUID" in MIGRATION
    assert "EXCEPTION WHEN unique_violation" in MIGRATION


def test_only_private_chat_claims_legacy_conversation() -> None:
    assert "IF p_chat_type = 'single' THEN" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION claim_legacy_wecom_conversation" in MIGRATION
    assert "c.source = 'wecom'" in MIGRATION
    assert "FOR UPDATE SKIP LOCKED" in MIGRATION
