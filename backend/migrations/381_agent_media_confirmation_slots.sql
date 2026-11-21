-- Validate a server-derived batch slot without loosening the original call record schema.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.agent_task_direct_records_valid(p_state jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE binding jsonb; projected jsonb;
BEGIN
 IF jsonb_typeof(p_state->'call_bindings') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 FOR binding IN SELECT value FROM jsonb_array_elements(p_state->'call_bindings') WHERE value ? 'media_slot' LOOP
   IF COALESCE(binding->>'media_slot','') !~ '^object-[a-f0-9]{32}:(100|[1-9][0-9]?)$'
      OR binding->>'tool_name' NOT IN ('generate_image','generate_video') THEN RETURN false; END IF;
   IF NOT EXISTS (
     SELECT 1 FROM jsonb_array_elements(p_state->'artifacts') AS asset(value)
     JOIN LATERAL (
       SELECT CASE WHEN asset.value->'validations' @> '["content.object.v2"]'::jsonb
                   THEN (asset.value->>'inline_text')::jsonb END AS body
     ) AS registered ON registered.body IS NOT NULL
     WHERE registered.body->>'object_key'=split_part(binding->>'media_slot', ':', 1)
       AND registered.body->>'confirmation_tool'=binding->>'tool_name'
       AND jsonb_array_length(registered.body->'confirmation_items')>0
       AND jsonb_array_length(registered.body#>'{document,items}')>=split_part(binding->>'media_slot', ':', 2)::integer
       AND EXISTS(SELECT FROM jsonb_array_elements(p_state->'results') r
                  WHERE r->'artifact_ids' ? (asset.value->>'id'))
   ) THEN RETURN false; END IF;
 END LOOP;
 IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_state->'call_bindings') b
             GROUP BY b->>'step_id' HAVING count(DISTINCT COALESCE(b->>'media_slot',''))>1) THEN RETURN false; END IF;
 SELECT jsonb_agg(value-'media_slot' ORDER BY ordinal) INTO projected
   FROM jsonb_array_elements(p_state->'call_bindings') WITH ORDINALITY AS row(value,ordinal);
 RETURN COALESCE(public.agent_task_direct_records_valid_before377(
     public.agent_task_content_record_projection(
       jsonb_set(p_state,'{call_bindings}',COALESCE(projected,'[]'::jsonb)))),false);
END $$;

CREATE OR REPLACE FUNCTION public.agent_media_confirmed_index(p_message text)
RETURNS integer LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN token ~ '^[0-9]+$' THEN token::integer ELSE NULLIF(strpos('一二三四五六七八九十', token),0) END
 FROM regexp_match(btrim(p_message),
  '^(?:我\s*)?(?:确认|批准|同意)\s*第\s*(100|[1-9][0-9]?|[一二三四五六七八九十])\s*(?:个(?:方案|视频|图片)?|条|项|张|段|版|套)\s*(?:(?:[，,]\s*)?(?:可以)?(?:开始|继续|现在|直接)?(?:生成|制作|执行)(?:吧)?)?[。！!\s]*$') AS m
 CROSS JOIN LATERAL (SELECT m[1] AS token) AS selected
$$;
REVOKE ALL ON FUNCTION public.agent_media_confirmed_index(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_media_confirmed_index(text) TO everydayai,everydayai_owner;

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
RESET ROLE;
