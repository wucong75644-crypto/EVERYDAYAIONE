-- 211: Worker 全局知识种子原子替换能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _validate_global_knowledge_seed_payload(p_payload JSONB)
RETURNS VOID
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_node JSONB;
    v_edge JSONB;
BEGIN
    IF jsonb_typeof(p_payload) <> 'object'
       OR (SELECT COUNT(*) FROM jsonb_object_keys(p_payload)) <> 3
       OR NOT (p_payload ?& ARRAY['version', 'nodes', 'edges'])
       OR p_payload->>'version' <> '1'
       OR jsonb_typeof(p_payload->'nodes') <> 'array'
       OR jsonb_typeof(p_payload->'edges') <> 'array'
       OR jsonb_array_length(p_payload->'nodes') > 10000
       OR jsonb_array_length(p_payload->'edges') > 50000 THEN
        RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_PAYLOAD_INVALID'
            USING ERRCODE = '22023';
    END IF;

    FOR v_node IN SELECT value FROM jsonb_array_elements(p_payload->'nodes')
    LOOP
        IF jsonb_typeof(v_node) <> 'object'
           OR (SELECT COUNT(*) FROM jsonb_object_keys(v_node)) <> 9
           OR NOT (v_node ?& ARRAY[
               'seed_key', 'category', 'subcategory', 'node_type',
               'title', 'content', 'metadata', 'confidence', 'embedding'
           ])
           OR v_node->>'seed_key' !~ '^node:[0-9]+$'
           OR v_node->>'category' NOT IN ('model', 'tool', 'experience')
           OR v_node->>'node_type' NOT IN (
               'model', 'parameter', 'pattern', 'capability', 'performance',
               'routing_pattern', 'failure_pattern'
           )
           OR char_length(v_node->>'title') NOT BETWEEN 1 AND 100
           OR char_length(v_node->>'content') NOT BETWEEN 1 AND 1000
           OR jsonb_typeof(v_node->'metadata') <> 'object'
           OR jsonb_typeof(v_node->'confidence') <> 'number'
           OR (v_node->>'confidence')::DOUBLE PRECISION NOT BETWEEN 0 AND 1
           OR (
               jsonb_typeof(v_node->'subcategory') NOT IN ('string', 'null')
           ) THEN
            RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_NODE_INVALID'
                USING ERRCODE = '22023';
        END IF;
        IF jsonb_typeof(v_node->'embedding') NOT IN ('array', 'null')
           OR (
               jsonb_typeof(v_node->'embedding') = 'array'
               AND (
                   jsonb_array_length(v_node->'embedding') <> 1024
                   OR EXISTS (
                       SELECT 1
                         FROM jsonb_array_elements(
                             v_node->'embedding'
                         ) element
                        WHERE jsonb_typeof(element) <> 'number'
                   )
               )
           ) THEN
            RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_EMBEDDING_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_payload->'nodes') node
         GROUP BY node->>'seed_key'
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_NODE_DUPLICATE'
            USING ERRCODE = '22023';
    END IF;

    FOR v_edge IN SELECT value FROM jsonb_array_elements(p_payload->'edges')
    LOOP
        IF jsonb_typeof(v_edge) <> 'object'
           OR (SELECT COUNT(*) FROM jsonb_object_keys(v_edge)) <> 3
           OR NOT (v_edge ?& ARRAY[
               'source_key', 'target_key', 'relation_type'
           ])
           OR v_edge->>'relation_type' <> 'related_to'
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') node
                WHERE node->>'seed_key' = v_edge->>'source_key'
           )
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') node
                WHERE node->>'seed_key' = v_edge->>'target_key'
           ) THEN
            RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_EDGE_INVALID'
                USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_payload->'edges') edge
         GROUP BY edge->>'source_key', edge->>'target_key',
                  edge->>'relation_type'
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_EDGE_DUPLICATE'
            USING ERRCODE = '22023';
    END IF;
END;
$$;

CREATE FUNCTION worker_replace_global_knowledge_seed(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_node JSONB;
    v_edge JSONB;
    v_node_id UUID;
    v_node_ids JSONB := '{}'::JSONB;
    v_imported INTEGER := 0;
    v_edge_count INTEGER := 0;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR tenant_actor_user_id() IS NOT NULL
       OR tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    PERFORM _validate_global_knowledge_seed_payload(p_payload);
    PERFORM pg_advisory_xact_lock(
        hashtextextended('global-knowledge-seed', 0)
    );
    PERFORM 1
      FROM knowledge_nodes
     WHERE source = 'seed'
       AND org_id IS NULL
       AND owner_user_id IS NULL
     FOR UPDATE;
    IF EXISTS (
        SELECT 1
          FROM knowledge_edges edge
          JOIN knowledge_nodes endpoint
            ON endpoint.id IN (edge.source_id, edge.target_id)
         WHERE endpoint.source = 'seed'
           AND endpoint.org_id IS NULL
           AND endpoint.owner_user_id IS NULL
           AND NOT (
               edge.org_id IS NULL
               AND edge.owner_user_id IS NULL
               AND EXISTS (
                   SELECT 1 FROM knowledge_nodes source_node
                    WHERE source_node.id = edge.source_id
                      AND source_node.source = 'seed'
                      AND source_node.org_id IS NULL
                      AND source_node.owner_user_id IS NULL
               )
               AND EXISTS (
                   SELECT 1 FROM knowledge_nodes target_node
                    WHERE target_node.id = edge.target_id
                      AND target_node.source = 'seed'
                      AND target_node.org_id IS NULL
                      AND target_node.owner_user_id IS NULL
               )
           )
    ) THEN
        RAISE EXCEPTION 'GLOBAL_KNOWLEDGE_SEED_REFERENCED'
            USING ERRCODE = '23503';
    END IF;

    DELETE FROM knowledge_edges edge
     USING knowledge_nodes source_node, knowledge_nodes target_node
     WHERE edge.source_id = source_node.id
       AND edge.target_id = target_node.id
       AND edge.org_id IS NULL
       AND edge.owner_user_id IS NULL
       AND source_node.source = 'seed'
       AND source_node.org_id IS NULL
       AND source_node.owner_user_id IS NULL
       AND target_node.source = 'seed'
       AND target_node.org_id IS NULL
       AND target_node.owner_user_id IS NULL;
    DELETE FROM knowledge_nodes
     WHERE source = 'seed'
       AND org_id IS NULL
       AND owner_user_id IS NULL;

    FOR v_node IN SELECT value FROM jsonb_array_elements(p_payload->'nodes')
    LOOP
        INSERT INTO knowledge_nodes (
            category, subcategory, node_type, title, content, metadata,
            embedding, source, confidence, scope, content_hash, org_id,
            owner_user_id
        ) VALUES (
            v_node->>'category', v_node->>'subcategory',
            v_node->>'node_type', v_node->>'title', v_node->>'content',
            v_node->'metadata', (v_node->>'embedding')::vector, 'seed',
            (v_node->>'confidence')::DOUBLE PRECISION, 'global',
            md5(
                (v_node->>'category') || '|' || (v_node->>'title')
                || '|' || (v_node->>'content')
            ),
            NULL, NULL
        ) RETURNING id INTO v_node_id;
        v_node_ids := v_node_ids || jsonb_build_object(
            v_node->>'seed_key', v_node_id::TEXT
        );
        v_imported := v_imported + 1;
    END LOOP;

    FOR v_edge IN SELECT value FROM jsonb_array_elements(p_payload->'edges')
    LOOP
        INSERT INTO knowledge_edges (
            source_id, target_id, relation_type, org_id, owner_user_id
        ) VALUES (
            (v_node_ids->>(v_edge->>'source_key'))::UUID,
            (v_node_ids->>(v_edge->>'target_key'))::UUID,
            'related_to', NULL, NULL
        );
        v_edge_count := v_edge_count + 1;
    END LOOP;
    RETURN jsonb_build_object(
        'outcome', 'replaced',
        'imported_count', v_imported,
        'edge_count', v_edge_count
    );
END;
$$;

REVOKE ALL ON FUNCTION
    _validate_global_knowledge_seed_payload(JSONB),
    worker_replace_global_knowledge_seed(JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION worker_replace_global_knowledge_seed(JSONB)
TO everydayai_worker;

RESET ROLE;
