-- New authenticated user input opens a bounded window; old operations and state remain intact.
SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
 old_fragment text := $old$   AND (logical.state->>'model_rounds_used')::int<(logical.state->>'model_round_limit')::int
$old$;
 new_fragment text := $new$$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$candidate-ARRAY['revision','status','model_rounds_used']$old$;
 new_fragment text := $new$candidate-ARRAY['revision','status','model_rounds_used','execution_window']$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$s.state-ARRAY['revision','status','model_rounds_used']$old$;
 new_fragment text := $new$s.state-ARRAY['revision','status','model_rounds_used','execution_window']$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$(CASE WHEN s.state->'model_directed'='true'::jsonb THEN greatest(1,COALESCE((fresh->>'next_model_round')::integer,1)) ELSE 1 END)$old$;
 new_fragment text := $new$greatest(1,COALESCE((fresh->>'next_model_round')::integer,1))$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$    OR p_payload->'_task_followup_input_checkpoint' IS DISTINCT FROM fresh$old$;
 new_fragment text := $new$    OR candidate->'execution_window' IS DISTINCT FROM jsonb_build_object('input_message_id',t.input_message_id,'model_rounds_used',greatest(1,COALESCE((fresh->>'next_model_round')::integer,1)),'correction_count',0)
    OR p_payload->'next_model_round' IS DISTINCT FROM to_jsonb(greatest(1,COALESCE((fresh->>'next_model_round')::integer,1)))
    OR p_payload->'_historical_image_catalog_v1' IS DISTINCT FROM fresh->'_historical_image_catalog_v1'
    OR p_payload->'_task_followup_input_checkpoint' IS DISTINCT FROM fresh$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$'_request_response_v1'])$old$;
 new_fragment text := $new$'_request_response_v1','_historical_image_catalog_v1'])$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected execution window definition: %', left(old_fragment,100); END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
 old_fragment text := $old$'state',s.state,'checkpoint',cp,$old$;
 new_fragment text := $new$'execution_window_version',1,'state',s.state,'checkpoint',cp,$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected window reader definition'; END IF;
 EXECUTE replace(definition,old_fragment,new_fragment);
END $migration$;
RESET ROLE;
