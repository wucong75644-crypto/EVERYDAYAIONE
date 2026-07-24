-- 回滚 154：WeCom 服务必须先切回旧数据库角色。

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID),
    stage_wecom_attachment_v2(
        UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
        BIGINT, JSONB, UUID
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
    ),
    update_wecom_conversation_setting(UUID, UUID, TEXT, TEXT, UUID),
    record_user_activity(
        UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
    )
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

DROP FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
);
DROP FUNCTION update_wecom_conversation_setting(
    UUID, UUID, TEXT, TEXT, UUID
);
DROP FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
);
DROP FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
);
DROP FUNCTION stage_wecom_attachment_v2(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
);
DROP FUNCTION resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID);

ALTER FUNCTION _record_user_activity_core(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) RENAME TO record_user_activity;
ALTER FUNCTION _update_wecom_conversation_setting_core(
    UUID, UUID, TEXT, TEXT, UUID
) RENAME TO update_wecom_conversation_setting;
ALTER FUNCTION _enqueue_wecom_generation_turn_v2_core(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) RENAME TO enqueue_wecom_generation_turn_v2;
ALTER FUNCTION _enqueue_wecom_generation_turn_v2_core(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) RENAME TO enqueue_wecom_generation_turn_v2;
ALTER FUNCTION _stage_wecom_attachment_v2_core(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
) RENAME TO stage_wecom_attachment_v2;
ALTER FUNCTION _resolve_wecom_conversation_core(
    UUID, TEXT, TEXT, TEXT, UUID
) RENAME TO resolve_wecom_conversation;

ALTER FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) RESET search_path;
ALTER FUNCTION update_wecom_conversation_setting(
    UUID, UUID, TEXT, TEXT, UUID
) SET search_path = public;
ALTER FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) SET search_path = public;
ALTER FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) SET search_path = public;
ALTER FUNCTION stage_wecom_attachment_v2(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
) SET search_path = public;
ALTER FUNCTION resolve_wecom_conversation(
    UUID, TEXT, TEXT, TEXT, UUID
) SET search_path = public;

GRANT EXECUTE ON FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) TO everydayai_runtime, everydayai_worker;
GRANT INSERT ON TABLE user_activity_events TO everydayai_runtime;

DROP FUNCTION _assert_wecom_message_scope(UUID, UUID);

RESET ROLE;
