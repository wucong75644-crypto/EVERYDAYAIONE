"""用户资产历史回填使用的固定 SQL。"""

SOURCE_QUERIES = {
    "image_generations": """
        SELECT g.id, g.created_at, g.user_id AS actor_user_id, g.org_id,
               g.conversation_id, g.model_id, g.prompt, g.image_url AS url
        FROM image_generations g
        WHERE (%(cursor_at)s::timestamptz IS NULL OR
               (g.created_at, g.id) >
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
        ORDER BY g.created_at, g.id LIMIT %(batch_size)s
    """,
    "tasks": """
        SELECT t.id, t.created_at, t.user_id AS actor_user_id, t.org_id,
               t.conversation_id, t.model_id, t.type, t.request_params,
               t.result, t.result_data, t.assistant_message_id,
               c.scope_type, b.corp_id, b.external_chat_id
        FROM tasks t
        LEFT JOIN conversations c ON c.id = t.conversation_id
        LEFT JOIN conversation_channel_bindings b ON b.conversation_id = t.conversation_id
        WHERE t.status = 'completed' AND t.type IN ('image', 'video')
          AND (%(cursor_at)s::timestamptz IS NULL OR
               (t.created_at, t.id) >
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
        ORDER BY t.created_at, t.id LIMIT %(batch_size)s
    """,
    "assistant_messages": """
        SELECT m.id, m.created_at, m.org_id, m.conversation_id, m.content,
               m.generation_params, COALESCE(t.user_id, c.user_id)
                   AS actor_user_id,
               c.scope_type, b.corp_id, b.external_chat_id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN tasks t ON t.assistant_message_id = m.id
        LEFT JOIN conversation_channel_bindings b ON b.conversation_id = m.conversation_id
        WHERE m.role::text = 'assistant'
          AND (%(cursor_at)s::timestamptz IS NULL OR
               (m.created_at, m.id) >
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
        ORDER BY m.created_at, m.id LIMIT %(batch_size)s
    """,
    "user_messages": """
        SELECT m.id, m.created_at, m.org_id, m.conversation_id, m.content,
               COALESCE(m.sender_user_id, c.user_id) AS actor_user_id,
               c.scope_type, b.corp_id, b.external_chat_id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN conversation_channel_bindings b ON b.conversation_id = m.conversation_id
        WHERE m.role::text = 'user'
          AND (%(cursor_at)s::timestamptz IS NULL OR
               (m.created_at, m.id) >
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
        ORDER BY m.created_at, m.id LIMIT %(batch_size)s
    """,
    "attachments": """
        SELECT a.id, a.created_at, a.org_id, a.conversation_id,
               a.source_message_id, a.sender_user_id AS actor_user_id,
               a.original_name AS name, a.url, a.workspace_path,
               a.storage_scope, a.mime_type, a.size,
               b.corp_id, b.external_chat_id
        FROM conversation_attachment_refs a
        LEFT JOIN conversation_channel_bindings b ON b.conversation_id = a.conversation_id
        WHERE a.status = 'ready'
          AND (%(cursor_at)s::timestamptz IS NULL OR
               (a.created_at, a.id) >
               (%(cursor_at)s::timestamptz, %(cursor_id)s::uuid))
        ORDER BY a.created_at, a.id LIMIT %(batch_size)s
    """,
}

RPC_SQL = """
    SELECT register_user_asset(
        %(p_org_id)s, %(p_storage_scope)s, %(p_storage_owner_key)s,
        %(p_storage_provider)s, %(p_storage_key)s, %(p_media_type)s,
        %(p_original_url)s, %(p_thumbnail_url)s, %(p_download_url)s,
        %(p_workspace_path)s, %(p_name)s, %(p_mime_type)s, %(p_size)s,
        %(p_content_sha256)s, %(p_asset_metadata)s, %(p_ref_key)s,
        %(p_actor_user_id)s, %(p_source_type)s, %(p_source_kind)s,
        %(p_ref_kind)s, %(p_conversation_id)s, %(p_source_message_id)s,
        %(p_source_task_id)s, %(p_source_generation_id)s,
        %(p_source_attachment_id)s, %(p_content_index)s, %(p_model_id)s,
        %(p_prompt)s, %(p_ref_metadata)s, %(p_created_at)s
    )
"""
