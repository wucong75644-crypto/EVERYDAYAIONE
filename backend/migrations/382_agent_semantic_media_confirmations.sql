-- Bind semantic decisions to authenticated user events and immutable saved items.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.agent_task_direct_operation_current_legacy381(p_state jsonb,p_checkpoint jsonb,p_operation_id text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 WITH control AS (
   SELECT seen.value AS event_id, entry.ordinal AS ordinal,
     CASE WHEN NOT entry.item ? 'attachments' THEN public.agent_media_confirmed_index(entry.item->>'message') END AS approved_item
   FROM jsonb_array_elements_text(COALESCE(p_checkpoint->'_task_direct_seen','[]'::jsonb)) seen(value)
   LEFT JOIN jsonb_array_elements(COALESCE(p_checkpoint#>'{_task_user_changes,items}','[]'::jsonb))
     WITH ORDINALITY AS entry(item,ordinal)
      ON entry.item->>'event_id'=seen.value AND entry.item->'delivered'='true'::jsonb
 ), current_input AS (
   SELECT public.agent_task_direct_input_hash(p_checkpoint) AS checksum
 )
 SELECT CASE WHEN p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN true ELSE EXISTS(
   SELECT 1 FROM jsonb_array_elements(p_state->'operations') op
   JOIN jsonb_array_elements(p_state->'call_bindings') b ON b->>'step_id'=op->>'step_id'
   CROSS JOIN current_input
   WHERE op->>'id'=p_operation_id AND b->>'tool_name'=op->>'tool_name'
     AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(op->>'step_id')
     AND (
       b->>'input_sha256'=current_input.checksum
       OR (
         op->>'status'='accepted' AND NULLIF(op->>'receipt_ref','') IS NOT NULL
         AND b->>'media_slot' ~ '^object-[a-f0-9]{32}:(100|[1-9][0-9]?)$'
         AND NOT EXISTS(SELECT FROM control WHERE ordinal IS NULL)
         AND EXISTS(
           SELECT FROM jsonb_array_elements(p_state->'call_bindings') sibling
           WHERE sibling->>'tool_name'=b->>'tool_name'
             AND sibling->>'input_sha256'=current_input.checksum
             AND split_part(sibling->>'media_slot',':',1)=split_part(b->>'media_slot',':',1)
             AND sibling->>'media_slot'<>b->>'media_slot'
             AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(sibling->>'step_id')
             AND split_part(sibling->>'media_slot',':',2)=(SELECT approved_item::text FROM control ORDER BY ordinal DESC LIMIT 1)
         )
         -- Every new directive since this effect's original proof must be the
         -- user's named approval of a different item. An attachment or other
         -- instruction still closes the original input fence.
         AND EXISTS(
           SELECT FROM control cutoff
           WHERE b->>'input_sha256'=public.agent_task_direct_input_hash(jsonb_build_object(
             '_task_direct_seen',(SELECT COALESCE(jsonb_agg(prior.event_id),'[]'::jsonb)
                                   FROM control prior WHERE prior.ordinal<cutoff.ordinal)))
             AND NOT EXISTS(SELECT FROM control changed WHERE changed.ordinal>=cutoff.ordinal
                AND (changed.approved_item IS NULL OR changed.approved_item::text=split_part(b->>'media_slot',':',2)))
         )
       )
     )) END
$$;
CREATE OR REPLACE FUNCTION public.agent_media_decision_items(p_state jsonb,p_checkpoint jsonb,p_event_id text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE directive jsonb; event jsonb; registered jsonb; item jsonb; matches integer;
BEGIN
 IF p_checkpoint#>>'{_media_decisions_v1,version}' IS DISTINCT FROM '1'
    OR jsonb_typeof(p_checkpoint#>'{_media_decisions_v1,items}') IS DISTINCT FROM 'array' THEN RETURN '[]'::jsonb; END IF;
 SELECT count(*) INTO matches FROM jsonb_array_elements(p_checkpoint#>'{_media_decisions_v1,items}') p
   WHERE p->>'event_id'=p_event_id;
 IF matches<>1 THEN RETURN '[]'::jsonb; END IF;
 SELECT p INTO directive FROM jsonb_array_elements(p_checkpoint#>'{_media_decisions_v1,items}') p WHERE p->>'event_id'=p_event_id;
 SELECT e INTO event FROM jsonb_array_elements(COALESCE(p_checkpoint#>'{_task_user_changes,items}','[]'::jsonb)) e
   WHERE e->>'event_id'=p_event_id AND e->'delivered'='true'::jsonb AND NOT e ? 'attachments';
 IF event IS NULL OR directive->>'owner_task_id' IS DISTINCT FROM event->>'owner_task_id'
    OR directive->>'message_sha256' IS DISTINCT FROM event->>'message_sha256'
    OR event->>'message_sha256' IS DISTINCT FROM encode(sha256(convert_to(event->>'message','UTF8')),'hex')
    OR COALESCE(directive->>'message_sha256','') !~ '^[a-f0-9]{64}$'
    OR COALESCE(directive->>'tool','') NOT IN ('generate_image','generate_video')
    OR jsonb_typeof(directive->'approved_items') IS DISTINCT FROM 'array'
    OR jsonb_array_length(directive->'approved_items') NOT BETWEEN 1 AND 100 THEN RETURN '[]'::jsonb; END IF;
 SELECT (a->>'inline_text')::jsonb INTO registered FROM jsonb_array_elements(p_state->'artifacts') a
   WHERE a->>'id'=directive->>'artifact_id' AND a->'validations' @> '["content.object.v2"]'::jsonb
     AND EXISTS(SELECT FROM jsonb_array_elements(p_state->'results') r WHERE r->'artifact_ids' ? (a->>'id'));
 IF registered IS NULL OR registered->>'object_key' IS DISTINCT FROM directive->>'object_key'
    OR registered->'revision' IS DISTINCT FROM directive->'revision'
    OR registered->>'confirmation_tool' IS DISTINCT FROM directive->>'tool'
    OR jsonb_typeof(registered->'confirmation_items') IS DISTINCT FROM 'array'
    OR jsonb_array_length(registered->'confirmation_items')=0 THEN RETURN '[]'::jsonb; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(directive->'approved_items') LOOP
   IF jsonb_typeof(item)<>'number' OR item::text !~ '^(100|[1-9][0-9]?)$'
      OR NOT registered->'confirmation_items' @> jsonb_build_array(item)
      OR jsonb_array_length(registered#>'{document,items}') < (item::text)::integer THEN RETURN '[]'::jsonb; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value) FROM jsonb_array_elements(directive->'approved_items'))<>jsonb_array_length(directive->'approved_items') THEN RETURN '[]'::jsonb; END IF;
 RETURN directive->'approved_items';
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN RETURN '[]'::jsonb;
END $$;
REVOKE ALL ON FUNCTION public.agent_media_decision_items(jsonb,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_media_decision_items(jsonb,jsonb,text) TO everydayai,everydayai_owner;

CREATE OR REPLACE FUNCTION public.agent_media_slot_proof_valid(p_state jsonb,p_binding jsonb,p_proof jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE registered jsonb;
BEGIN
 IF p_proof IS NULL OR p_proof->>'tool' IS DISTINCT FROM p_binding->>'tool_name'
    OR p_proof->>'object_key' IS DISTINCT FROM split_part(p_binding->>'media_slot',':',1)
    OR p_proof->>'item_index' IS DISTINCT FROM split_part(p_binding->>'media_slot',':',2)
    OR COALESCE(p_proof->>'item_index','') !~ '^(100|[1-9][0-9]?)$' THEN RETURN false; END IF;
 SELECT (a->>'inline_text')::jsonb INTO registered FROM jsonb_array_elements(p_state->'artifacts') a
   WHERE a->>'id'=p_proof->>'artifact_id' AND a->'validations' @> '["content.object.v2"]'::jsonb
     AND EXISTS(SELECT FROM jsonb_array_elements(p_state->'results') r WHERE r->'artifact_ids' ? (a->>'id'));
 RETURN registered IS NOT NULL AND registered->>'object_key'=p_proof->>'object_key'
   AND registered->'revision'=p_proof->'revision' AND registered->>'confirmation_tool'=p_proof->>'tool'
   AND jsonb_array_length(registered->'confirmation_items')>0
   AND jsonb_array_length(registered#>'{document,items}')>=(p_proof->>'item_index')::integer;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.agent_media_slot_proof_valid(jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_media_slot_proof_valid(jsonb,jsonb,jsonb) TO everydayai,everydayai_owner;

CREATE OR REPLACE FUNCTION public.agent_task_direct_operation_current(p_state jsonb,p_checkpoint jsonb,p_operation_id text)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT p_checkpoint ? '_media_decisions_v1' THEN
   RETURN public.agent_task_direct_operation_current_legacy381(p_state,p_checkpoint,p_operation_id);
 END IF;
 IF p_checkpoint#>>'{_media_decisions_v1,version}' IS DISTINCT FROM '1'
    OR jsonb_typeof(p_checkpoint#>'{_media_decisions_v1,items}') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 RETURN CASE WHEN p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN true ELSE EXISTS(
   WITH control AS MATERIALIZED (
     SELECT seen.value AS event_id, entry.ordinal AS ordinal,
       public.agent_media_decision_items(p_state,p_checkpoint,seen.value) AS approved_items,
       proof.item->>'artifact_id' AS artifact_id, proof.item->>'tool' AS tool
     FROM jsonb_array_elements_text(COALESCE(p_checkpoint->'_task_direct_seen','[]'::jsonb)) seen(value)
     LEFT JOIN jsonb_array_elements(COALESCE(p_checkpoint#>'{_task_user_changes,items}','[]'::jsonb))
       WITH ORDINALITY AS entry(item,ordinal) ON entry.item->>'event_id'=seen.value AND entry.item->'delivered'='true'::jsonb
     LEFT JOIN LATERAL (SELECT p AS item FROM jsonb_array_elements(p_checkpoint#>'{_media_decisions_v1,items}') p
       WHERE p->>'event_id'=seen.value LIMIT 1) proof ON true
   ), current_input AS (SELECT public.agent_task_direct_input_hash(p_checkpoint) AS checksum)
   SELECT 1 FROM jsonb_array_elements(p_state->'operations') op
   JOIN jsonb_array_elements(p_state->'call_bindings') b ON b->>'step_id'=op->>'step_id'
   CROSS JOIN current_input
   WHERE op->>'id'=p_operation_id AND b->>'tool_name'=op->>'tool_name'
     AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(op->>'step_id')
     AND (b->>'input_sha256'=current_input.checksum OR (
       op->>'status'='accepted' AND NULLIF(op->>'receipt_ref','') IS NOT NULL
       AND b->>'media_slot' ~ '^object-[a-f0-9]{32}:(100|[1-9][0-9]?)$'
       AND public.agent_media_slot_proof_valid(p_state,b,p_checkpoint#>'{_media_decisions_v1,bindings}'->(b->>'call_id'))
       AND NOT EXISTS(SELECT FROM control WHERE ordinal IS NULL)
       AND EXISTS(
         SELECT FROM jsonb_array_elements(p_state->'call_bindings') sibling
         WHERE sibling->>'tool_name'=b->>'tool_name' AND sibling->>'input_sha256'=current_input.checksum
           AND public.agent_media_slot_proof_valid(p_state,sibling,p_checkpoint#>'{_media_decisions_v1,bindings}'->(sibling->>'call_id'))
           AND split_part(sibling->>'media_slot',':',1)=split_part(b->>'media_slot',':',1)
           AND sibling->>'media_slot'<>b->>'media_slot'
           AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(sibling->>'step_id')
           AND p_checkpoint#>'{_media_decisions_v1,bindings}'->(sibling->>'call_id')->>'artifact_id' IS NOT NULL
           AND p_checkpoint#>'{_media_decisions_v1,bindings}'->(sibling->>'call_id')->>'artifact_id'
               =p_checkpoint#>'{_media_decisions_v1,bindings}'->(b->>'call_id')->>'artifact_id'
           AND (SELECT approved_items FROM control ORDER BY ordinal DESC LIMIT 1) @>
               jsonb_build_array((p_checkpoint#>'{_media_decisions_v1,bindings}'->(sibling->>'call_id')->'item_index'))
       )
       AND EXISTS(
         SELECT FROM control cutoff
         WHERE b->>'input_sha256'=public.agent_task_direct_input_hash(jsonb_build_object('_task_direct_seen',
           (SELECT COALESCE(jsonb_agg(prior.event_id),'[]'::jsonb) FROM control prior WHERE prior.ordinal<cutoff.ordinal)))
           AND NOT EXISTS(SELECT FROM control changed WHERE changed.ordinal>=cutoff.ordinal AND (
             changed.approved_items='[]'::jsonb OR changed.tool IS DISTINCT FROM b->>'tool_name'
             OR changed.artifact_id IS DISTINCT FROM p_checkpoint#>'{_media_decisions_v1,bindings}'->(b->>'call_id')->>'artifact_id'
             OR changed.approved_items @> jsonb_build_array(p_checkpoint#>'{_media_decisions_v1,bindings}'->(b->>'call_id')->'item_index')
           ))
       )
     ))
 ) END;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_direct_operation_current_legacy381(jsonb,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_operation_current_legacy381(jsonb,jsonb,text) TO everydayai,everydayai_owner;

RESET ROLE;
