SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
 original text := $old$jsonb_build_object('item_index',idx,'title',item->>'title',
                'options',COALESCE(item->'options','{}'::jsonb),
                'has_bound_inputs',EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(item->'materials','[]'::jsonb)) material
                    WHERE material->>'usage'='input'))$old$;
 replacement text := $new$jsonb_build_object('item_index',idx,'title',item->>'title')$new$;
BEGIN
 IF strpos(definition,original)=0 THEN RAISE EXCEPTION 'Unexpected content item catalog definition'; END IF;
 EXECUTE replace(definition,original,replacement);
END $migration$;
RESET ROLE;
