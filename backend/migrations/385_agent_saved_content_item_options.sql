-- Expose facts from each saved item, not an inferred group-wide specification.
-- No state, identity, authorization or historical selection rules change.
SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
 original text := $old$jsonb_build_object('item_index',idx,'title',item->>'title')$old$;
 replacement text := $new$jsonb_build_object('item_index',idx,'title',item->>'title',
                'options',COALESCE(item->'options','{}'::jsonb),
                'has_bound_inputs',EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(item->'materials','[]'::jsonb)) material
                    WHERE material->>'usage'='input'))$new$;
BEGIN
 IF strpos(definition,original)=0 THEN RAISE EXCEPTION 'Unexpected content item catalog definition'; END IF;
 EXECUTE replace(definition,original,replacement);
END $migration$;
RESET ROLE;
