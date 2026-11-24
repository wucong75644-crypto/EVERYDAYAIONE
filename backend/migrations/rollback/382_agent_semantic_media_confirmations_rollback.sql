-- Do not erase a live new-format execution fence.
SET LOCAL ROLE everydayai;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.conversation_turn_checkpoints cp JOIN public.tasks t ON t.id=cp.task_id
   WHERE cp.state ? '_media_decisions_v1' AND t.status IN ('pending','running','paused')) THEN
   RAISE EXCEPTION 'ACTIVE_SEMANTIC_MEDIA_CHECKPOINT_REQUIRES_FORWARD_RECOVERY';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.agent_task_direct_operation_current(p_state jsonb,p_checkpoint jsonb,p_operation_id text)
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
DROP FUNCTION IF EXISTS public.agent_media_decision_items(jsonb,jsonb,text);
DROP FUNCTION IF EXISTS public.agent_media_slot_proof_valid(jsonb,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.agent_task_direct_operation_current_legacy381(jsonb,jsonb,text);
RESET ROLE;
