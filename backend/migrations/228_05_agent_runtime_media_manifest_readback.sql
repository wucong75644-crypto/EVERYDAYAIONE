SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_prepared_media_video_pricing_facts (pricing_revision TEXT NOT NULL, model_id TEXT NOT NULL, duration_seconds INTEGER NOT NULL CHECK (duration_seconds IN (10,15,25)), user_credits INTEGER NOT NULL CHECK (user_credits > 0), requires_image_input BOOLEAN NOT NULL, active BOOLEAN NOT NULL, fact_hash TEXT NOT NULL CHECK (fact_hash ~ '^[0-9a-f]{64}$'), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY (pricing_revision, model_id, duration_seconds) );
WITH facts(model_id,duration_seconds,user_credits,requires_image_input) AS (VALUES ('sora-2-text-to-video',10,31,FALSE), ('sora-2-text-to-video',15,46,FALSE), ('sora-2-image-to-video',10,31,TRUE), ('sora-2-image-to-video',15,46,TRUE), ('sora-2-pro-storyboard',10,91,FALSE), ('sora-2-pro-storyboard',15,136,FALSE), ('sora-2-pro-storyboard',25,226,FALSE) ) INSERT INTO agent_runtime_prepared_media_video_pricing_facts(pricing_revision,model_id,duration_seconds,user_credits, requires_image_input,active,fact_hash ) SELECT 'kie-video-pricing-v1',model_id,duration_seconds,user_credits, requires_image_input,TRUE,encode(digest(convert_to(jsonb_build_object('pricing_revision','kie-video-pricing-v1','model_id',model_id, 'duration_seconds',duration_seconds,'user_credits',user_credits, 'requires_image_input',requires_image_input,'active',TRUE )::TEXT,'UTF8'),'sha256'),'hex') FROM facts;
CREATE TRIGGER agent_runtime_prepared_media_video_pricing_immutable BEFORE INSERT OR UPDATE OR DELETE ON agent_runtime_prepared_media_video_pricing_facts FOR EACH ROW EXECUTE FUNCTION _agent_runtime_media_pricing_immutable_v1();
CREATE TABLE agent_runtime_prepared_media_action_bindings (action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT, task_id UUID NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT, session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT, run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT, model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT, org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT, user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT, input_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT, output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT, media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video')), action_request_hash TEXT NOT NULL CHECK (action_request_hash ~ '^[0-9a-f]{64}$'), task_request_hash TEXT NOT NULL CHECK (task_request_hash ~ '^[0-9a-f]{64}$'), reference_manifest_hash TEXT NOT NULL CHECK (reference_manifest_hash ~ '^[0-9a-f]{64}$'), provider_request_hash TEXT NOT NULL CHECK (provider_request_hash ~ '^[0-9a-f]{64}$'), pricing_revision TEXT NOT NULL, pricing_model_id TEXT NOT NULL, pricing_key TEXT NOT NULL, pricing_fact_hash TEXT NOT NULL CHECK (pricing_fact_hash ~ '^[0-9a-f]{64}$'), unit_credits INTEGER NOT NULL CHECK (unit_credits > 0), credit_transaction_id UUID NOT NULL UNIQUE REFERENCES credit_transactions(id) ON DELETE RESTRICT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() );
ALTER TABLE agent_runtime_media_action_bindings
    ADD COLUMN provider_request_canonical_hash TEXT
    CHECK (provider_request_canonical_hash IS NULL OR provider_request_canonical_hash ~ '^[0-9a-f]{64}$');
CREATE TABLE agent_runtime_media_owner_readiness (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    runtime_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    provider_probe_passed BOOLEAN NOT NULL DEFAULT FALSE,
    production_ready BOOLEAN NOT NULL DEFAULT FALSE,
    projection_owner_ready BOOLEAN NOT NULL DEFAULT FALSE,
    projection_worker_id TEXT,
    projection_revision TEXT,
    projection_heartbeat_at TIMESTAMPTZ,
    projection_heartbeat_ttl_seconds INTEGER NOT NULL DEFAULT 30
        CHECK (projection_heartbeat_ttl_seconds BETWEEN 5 AND 300),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (NOT production_ready OR (runtime_enabled AND provider_probe_passed)),
    CHECK (NOT projection_owner_ready OR (
        NULLIF(btrim(projection_worker_id),'') IS NOT NULL
        AND NULLIF(btrim(projection_revision),'') IS NOT NULL
        AND projection_heartbeat_at IS NOT NULL
    ))
);
INSERT INTO agent_runtime_media_owner_readiness(singleton) VALUES(TRUE);
ALTER TABLE agent_runtime_prepared_media_video_pricing_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_prepared_media_action_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_owner_readiness ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_prepared_media_video_pricing_owner_all ON agent_runtime_prepared_media_video_pricing_facts FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_prepared_media_bindings_owner_all ON agent_runtime_prepared_media_action_bindings FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_media_owner_readiness_owner_all ON agent_runtime_media_owner_readiness FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_prepared_media_video_pricing_facts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_prepared_media_action_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_owner_readiness FORCE ROW LEVEL SECURITY;
CREATE FUNCTION _agent_runtime_media_owner_readiness_v1()
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE control agent_runtime_media_owner_readiness%ROWTYPE;
projection_heartbeat_fresh BOOLEAN;
BEGIN
SELECT * INTO control FROM agent_runtime_media_owner_readiness WHERE singleton;
IF control.singleton IS NULL THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_MISSING' USING ERRCODE='55000';
END IF;
projection_heartbeat_fresh:=(
    control.projection_owner_ready
    AND control.projection_heartbeat_at IS NOT NULL
    AND control.projection_heartbeat_at >= statement_timestamp()
        - make_interval(secs=>control.projection_heartbeat_ttl_seconds)
);
RETURN jsonb_build_object(
    'runtime_enabled',control.runtime_enabled,
    'provider_probe_passed',control.provider_probe_passed,
    'production_ready',control.production_ready,
    'projection_owner_ready',control.projection_owner_ready,
    'projection_worker_id',control.projection_worker_id,
    'projection_revision',control.projection_revision,
    'projection_heartbeat_at',control.projection_heartbeat_at,
    'projection_heartbeat_ttl_seconds',control.projection_heartbeat_ttl_seconds,
    'projection_heartbeat_fresh',projection_heartbeat_fresh,
    'ready',(control.runtime_enabled AND control.provider_probe_passed
        AND control.production_ready AND projection_heartbeat_fresh),
    'state_version',control.state_version
);
END;
$$;
-- 228_06 projection-owner contract: call on startup and refresh before the
-- returned heartbeat TTL expires; send p_ready=FALSE before an intentional drain.
CREATE FUNCTION record_agent_runtime_media_projection_readiness_v1(
    p_worker_id TEXT,
    p_projection_revision TEXT,
    p_ready BOOLEAN,
    p_heartbeat_ttl_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    IF session_user<>'everydayai_projection_worker'
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_SCOPE_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    IF NULLIF(btrim(p_worker_id),'') IS NULL
       OR length(btrim(p_worker_id))>128
       OR NULLIF(btrim(p_projection_revision),'') IS NULL
       OR length(btrim(p_projection_revision))>128
       OR p_ready IS NULL
       OR p_heartbeat_ttl_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_READINESS_INVALID'
            USING ERRCODE='22023';
    END IF;
    UPDATE agent_runtime_media_owner_readiness
       SET projection_owner_ready=p_ready,
           projection_worker_id=btrim(p_worker_id),
           projection_revision=btrim(p_projection_revision),
           projection_heartbeat_at=statement_timestamp(),
           projection_heartbeat_ttl_seconds=p_heartbeat_ttl_seconds,
           state_version=state_version+1,
           updated_at=clock_timestamp()
     WHERE singleton;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_MISSING'
            USING ERRCODE='55000';
    END IF;
    RETURN _agent_runtime_media_owner_readiness_v1();
END;
$$;
CREATE FUNCTION _agent_runtime_media_attempt_context_v2(p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID, p_expected_attempt_version BIGINT,p_request_hash TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE attempt agent_action_attempts%ROWTYPE;
action agent_actions%ROWTYPE;
runtime_session agent_runtime_sessions%ROWTYPE;
runtime_run agent_runs%ROWTYPE;
intent agent_action_dispatch_intents%ROWTYPE;
kill_context JSONB;
readiness JSONB;
BEGIN PERFORM _agent_runtime_media_worker_v1();
IF p_action_id IS NULL OR p_attempt_id IS NULL OR p_owner_token IS NULL OR NULLIF(btrim(p_worker_id),'') IS NULL OR p_expected_attempt_version IS NULL OR p_expected_attempt_version < 0 OR COALESCE(p_request_hash,'') !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ATTEMPT_SCOPE_INVALID' USING ERRCODE='42501';
END IF;
SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
SELECT * INTO action FROM agent_actions WHERE id=p_action_id;
SELECT * INTO runtime_session FROM agent_runtime_sessions WHERE id=action.session_id;
SELECT * INTO runtime_run FROM agent_runs WHERE id=action.run_id;
SELECT * INTO intent FROM agent_action_dispatch_intents WHERE attempt_id=attempt.id AND action_id=action.id;
IF attempt.id IS NULL OR action.id IS NULL OR runtime_session.id IS NULL OR runtime_run.id IS NULL OR intent.id IS NULL OR action.tool_name NOT IN ('generate_image','generate_video') OR attempt.action_id IS DISTINCT FROM action.id OR attempt.session_id IS DISTINCT FROM runtime_session.id OR attempt.run_id IS DISTINCT FROM runtime_run.id OR action.session_id IS DISTINCT FROM runtime_session.id OR action.run_id IS DISTINCT FROM runtime_run.id OR runtime_run.session_id IS DISTINCT FROM runtime_session.id OR attempt.org_id IS DISTINCT FROM action.org_id OR action.org_id IS DISTINCT FROM runtime_session.org_id OR runtime_run.org_id IS DISTINCT FROM runtime_session.org_id OR attempt.user_id IS DISTINCT FROM action.user_id OR action.user_id IS DISTINCT FROM runtime_session.user_id OR runtime_run.user_id IS DISTINCT FROM runtime_session.user_id OR attempt.worker_id IS DISTINCT FROM btrim(p_worker_id) OR attempt.state_version IS DISTINCT FROM p_expected_attempt_version OR attempt.request_hash IS DISTINCT FROM p_request_hash OR action.request_hash IS DISTINCT FROM p_request_hash OR intent.execution_token IS DISTINCT FROM attempt.execution_token OR intent.request_hash IS DISTINCT FROM p_request_hash OR intent.executor_type IS DISTINCT FROM 'runtime_media_generation:'||action.tool_name OR intent.executor_revision <> 1 OR NOT EXISTS (SELECT 1 FROM agent_policy_receipts receipt WHERE receipt.id=intent.policy_receipt_id AND receipt.action_id=action.id AND receipt.decision='allow' AND receipt.arguments_hash=action.arguments_hash AND receipt.executor_type=intent.executor_type AND receipt.executor_revision=intent.executor_revision ) OR NOT ((attempt.status IN ('claimed','dispatching') AND action.status='running' AND attempt.execution_token=p_owner_token AND attempt.lease_expires_at>clock_timestamp()) OR (attempt.status IN ('accepted','unknown') AND action.status=attempt.status AND attempt.reconciliation_token=p_owner_token AND attempt.reconciliation_lease_expires_at>clock_timestamp()) ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ATTEMPT_SCOPE_INVALID' USING ERRCODE='42501';
END IF;
readiness:=_agent_runtime_media_owner_readiness_v1();
IF attempt.status IN ('claimed','dispatching') AND (readiness->>'ready')::BOOLEAN IS NOT TRUE THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_OWNER_NOT_READY' USING ERRCODE='55000';
END IF;
IF attempt.status IN ('claimed','dispatching') THEN kill_context:=_agent_runtime_kill_epoch_context(attempt.id,attempt.execution_token,attempt.request_hash, attempt.state_version,'dispatch' );
IF kill_context->>'outcome' IS DISTINCT FROM 'allowed' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ATTEMPT_FENCED' USING ERRCODE='42501';
END IF;
END IF;
RETURN jsonb_build_object('action_id',action.id,'attempt_id',attempt.id, 'session_id',runtime_session.id,'run_id',runtime_run.id, 'model_step_id',action.model_step_id,'org_id',runtime_session.org_id, 'user_id',runtime_session.user_id, 'conversation_id',runtime_session.conversation_id, 'tool_name',action.tool_name, 'source',COALESCE(action.policy_snapshot->>'source','model_loop'), 'task_id',action.policy_snapshot->>'task_id', 'input_message_id',action.policy_snapshot->>'input_message_id', 'output_message_id',action.policy_snapshot->>'output_message_id' );
END;
$$;
CREATE FUNCTION _agent_runtime_media_resolved_images_v1(p_session_id UUID,p_input_message_id UUID ) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE runtime_session agent_runtime_sessions%ROWTYPE;
input_message messages%ROWTYPE;
manifest JSONB;
images JSONB;
image_count INTEGER;
BEGIN SELECT * INTO runtime_session FROM agent_runtime_sessions WHERE id=p_session_id;
SELECT * INTO input_message FROM messages WHERE id=p_input_message_id AND conversation_id=runtime_session.conversation_id AND org_id IS NOT DISTINCT FROM runtime_session.org_id;
IF runtime_session.id IS NULL OR input_message.id IS NULL OR input_message.role::TEXT<>'user' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INPUT_ANCHOR_INVALID' USING ERRCODE='42501';
END IF;
manifest:=_agent_runtime_media_input_manifest_v1(input_message.content);
image_count:=(manifest->>'image_count')::INTEGER;
SELECT COALESCE(jsonb_agg(jsonb_build_object('index',source.image_index,'url',source.resolved_url ) ORDER BY source.image_index),'[]'::JSONB) INTO images FROM (SELECT image_parts.image_index, CASE WHEN asset.id IS NOT NULL THEN COALESCE(asset.download_url,asset.original_url) WHEN attachment.id IS NOT NULL THEN attachment.url WHEN NULLIF(btrim(image_parts.part->>'asset_id'),'') IS NULL THEN NULLIF(btrim(COALESCE(image_parts.part->>'url',image_parts.part->>'original_url', image_parts.part->>'download_url' )),'') ELSE NULL END AS resolved_url FROM (SELECT part,row_number() OVER (ORDER BY ordinality)-1 AS image_index FROM jsonb_array_elements(input_message.content::JSONB) WITH ORDINALITY AS parts(part,ordinality) WHERE part->>'type'='image' ) image_parts LEFT JOIN user_assets asset ON pg_input_is_valid(image_parts.part->>'asset_id','uuid') AND asset.id=(image_parts.part->>'asset_id')::UUID AND asset.org_id IS NOT DISTINCT FROM runtime_session.org_id AND asset.storage_scope='user' AND asset.storage_owner_key=runtime_session.user_id::TEXT AND asset.storage_provider='workspace' AND asset.workspace_path=image_parts.part->>'workspace_path' AND asset.media_type='image' AND asset.status='ready' AND NULLIF(btrim(COALESCE(asset.download_url,asset.original_url)),'') IS NOT NULL LEFT JOIN conversation_attachment_refs attachment ON asset.id IS NULL AND pg_input_is_valid(image_parts.part->>'asset_id','uuid') AND attachment.id=(image_parts.part->>'asset_id')::UUID AND attachment.conversation_id=runtime_session.conversation_id AND attachment.org_id IS NOT DISTINCT FROM runtime_session.org_id AND attachment.workspace_path=image_parts.part->>'workspace_path' AND attachment.status='ready' AND attachment.reference_state IN ('active','referenced') AND NULLIF(btrim(attachment.url),'') IS NOT NULL ) source;
IF jsonb_array_length(images)<>image_count OR EXISTS (SELECT 1 FROM jsonb_array_elements(images) image WHERE COALESCE(image->>'url','') !~ '^https://[^[:space:]]+$' ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_INCOMPLETE' USING ERRCODE='42501';
END IF;
RETURN jsonb_build_object('manifest_hash',manifest->>'manifest_hash','images',images );
END;
$$;
CREATE FUNCTION _agent_runtime_kie_provider_request_v1(p_kind TEXT,p_request JSONB,p_reference_urls JSONB ) RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE model_id TEXT:=NULLIF(btrim(p_request->>'model'),'');
prompt TEXT:=NULLIF(btrim(p_request->>'prompt'),'');
aspect_ratio TEXT:=NULLIF(btrim(p_request->>'aspect_ratio'),'');
resolution TEXT:=NULLIF(btrim(p_request->>'resolution'),'');
output_format TEXT:=NULLIF(btrim(p_request->>'output_format'),'');
duration_seconds INTEGER;
provider_input JSONB;
BEGIN IF p_kind NOT IN ('image','video') OR jsonb_typeof(p_request)<>'object' OR jsonb_typeof(p_reference_urls)<>'array' OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_reference_urls) url WHERE jsonb_typeof(url)<>'string' OR trim(BOTH '"' FROM url::TEXT) !~ '^https://[^[:space:]]+$') THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END IF;
IF p_kind='image' THEN
IF model_id NOT IN ('google/nano-banana','google/nano-banana-edit','nano-banana-pro', 'gpt-image-2-text-to-image','gpt-image-2-image-to-image') OR prompt IS NULL OR length(prompt)>20000 THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END IF;
aspect_ratio:=COALESCE(aspect_ratio,'1:1');
resolution:=COALESCE(resolution,'1K');
output_format:=COALESCE(output_format,'png');
IF model_id IN ('google/nano-banana','google/nano-banana-edit') THEN
    IF length(prompt)>5000 OR aspect_ratio NOT IN ('1:1','2:3','3:2','3:4','4:3','4:5','5:4','9:16','16:9','21:9','auto') OR output_format NOT IN ('png','jpeg') THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    IF model_id='google/nano-banana' AND jsonb_array_length(p_reference_urls)<>0 THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REFERENCE_INVALID' USING ERRCODE='22023';
    END IF;
    IF model_id='google/nano-banana-edit' AND jsonb_array_length(p_reference_urls) NOT BETWEEN 1 AND 10 THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REFERENCE_INVALID' USING ERRCODE='22023';
    END IF;
ELSIF model_id='nano-banana-pro' THEN
    IF aspect_ratio NOT IN ('1:1','2:3','3:2','3:4','4:3','4:5','5:4','9:16','16:9','21:9','auto') OR resolution NOT IN ('1K','2K','4K') OR output_format NOT IN ('png','jpg') OR jsonb_array_length(p_reference_urls)>8 THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
ELSE
    IF aspect_ratio NOT IN ('1:1','2:3','3:2','3:4','4:3','4:5','5:4','9:16','16:9','21:9','auto') OR resolution NOT IN ('1K','2K','4K') THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    IF (model_id='gpt-image-2-image-to-image' AND jsonb_array_length(p_reference_urls) NOT BETWEEN 1 AND 16) OR (model_id='gpt-image-2-text-to-image' AND jsonb_array_length(p_reference_urls)<>0) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REFERENCE_INVALID' USING ERRCODE='22023';
    END IF;
END IF;
provider_input:=CASE model_id
    WHEN 'google/nano-banana' THEN jsonb_build_object('prompt',prompt,'aspect_ratio',aspect_ratio,'output_format',output_format)
    WHEN 'google/nano-banana-edit' THEN jsonb_build_object('prompt',prompt,'image_urls',p_reference_urls,'aspect_ratio',aspect_ratio,'output_format',output_format)
    WHEN 'nano-banana-pro' THEN jsonb_build_object('prompt',prompt,'image_input',p_reference_urls,'aspect_ratio',aspect_ratio,'resolution',resolution,'output_format',output_format)
    WHEN 'gpt-image-2-text-to-image' THEN jsonb_build_object('prompt',prompt,'aspect_ratio',aspect_ratio,'resolution',resolution)
    WHEN 'gpt-image-2-image-to-image' THEN jsonb_build_object('prompt',prompt,'input_urls',p_reference_urls,'aspect_ratio',aspect_ratio,'resolution',resolution)
END;
ELSE IF model_id NOT IN ('sora-2-text-to-video','sora-2-image-to-video', 'sora-2-pro-storyboard' ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END IF;
aspect_ratio:=COALESCE(aspect_ratio,'landscape');
IF aspect_ratio NOT IN ('portrait','landscape') THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END IF;
BEGIN duration_seconds:=COALESCE(NULLIF(p_request->>'duration','')::INTEGER, CASE WHEN COALESCE(NULLIF(p_request->>'n_frames','')::INTEGER,25)<=125 THEN 10 ELSE 15 END);
EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END;
IF duration_seconds NOT IN (10,15,25) OR (model_id<>'sora-2-pro-storyboard' AND duration_seconds=25) OR (model_id<>'sora-2-pro-storyboard' AND prompt IS NULL) OR (model_id='sora-2-image-to-video' AND jsonb_array_length(p_reference_urls)=0) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_INVALID' USING ERRCODE='22023';
END IF;
provider_input:=CASE model_id WHEN 'sora-2-text-to-video' THEN jsonb_build_object('prompt',prompt,'aspect_ratio',aspect_ratio, 'n_frames',duration_seconds::TEXT,'remove_watermark', COALESCE((p_request->>'remove_watermark')::BOOLEAN,TRUE)) WHEN 'sora-2-image-to-video' THEN jsonb_build_object('prompt',prompt,'image_urls',p_reference_urls, 'aspect_ratio',aspect_ratio,'n_frames',duration_seconds::TEXT, 'remove_watermark',COALESCE((p_request->>'remove_watermark')::BOOLEAN,TRUE)) WHEN 'sora-2-pro-storyboard' THEN jsonb_build_object('n_frames',duration_seconds::TEXT,'image_urls',p_reference_urls, 'aspect_ratio',aspect_ratio) END;
END IF;
RETURN jsonb_build_object('model',model_id,'input',provider_input);
END;
$$;
CREATE OR REPLACE FUNCTION submit_agent_runtime_media_action_v1(p_conversation_id UUID,p_org_id UUID,p_user_id UUID, p_scope_kind TEXT,p_scope_id TEXT,p_created_by_user_id UUID, p_agent_definition_id TEXT,p_agent_definition_revision TEXT, p_task_id UUID,p_input_message_id UUID,p_output_message_id UUID, p_turn_id UUID,p_tool_name TEXT,p_arguments JSONB,p_model_id TEXT, p_model_provider TEXT,p_model_revision TEXT,p_catalog_revision TEXT, p_policy_revision TEXT,p_idempotency_key TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE prepared_task tasks%ROWTYPE;
session_result JSONB;
runtime_result JSONB;
safe_arguments JSONB;
readiness JSONB;
runtime_action agent_actions%ROWTYPE;
runtime_session agent_runtime_sessions%ROWTYPE;
prepared_binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
prepared_result JSONB;
BEGIN PERFORM _assert_agent_runtime_actor(FALSE);
IF p_tool_name NOT IN ('generate_image','generate_video') OR jsonb_typeof(p_arguments)<>'object' OR NULLIF(btrim(p_idempotency_key),'') IS NULL OR p_task_id IS NULL OR p_input_message_id IS NULL OR p_output_message_id IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
END IF;
SELECT * INTO prepared_task FROM tasks
 WHERE id=p_task_id
   AND conversation_id=p_conversation_id
   AND user_id=p_user_id
   AND org_id IS NOT DISTINCT FROM p_org_id
   AND type::TEXT=(CASE WHEN p_tool_name='generate_image' THEN 'image' ELSE 'video' END)
   AND input_message_id=p_input_message_id
   AND assistant_message_id=p_output_message_id
 FOR UPDATE;
IF prepared_task.id IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
END IF;
IF COALESCE((prepared_task.delivery_context->>'runtime')::BOOLEAN,FALSE) THEN
    IF prepared_task.delivery_context->>'runtime_owner' IS DISTINCT FROM 'action_loop' OR COALESCE(pg_input_is_valid(prepared_task.delivery_context->>'runtime_action_id','uuid'),FALSE) IS NOT TRUE OR COALESCE(pg_input_is_valid(prepared_task.delivery_context->>'runtime_run_id','uuid'),FALSE) IS NOT TRUE THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    SELECT * INTO prepared_binding
      FROM agent_runtime_prepared_media_action_bindings
     WHERE task_id=prepared_task.id
       AND action_id=(prepared_task.delivery_context->>'runtime_action_id')::UUID;
    IF prepared_binding.action_id IS NOT NULL THEN
        RETURN jsonb_build_object('outcome','already_exists','runtime_owned',TRUE, 'action_id',prepared_task.delivery_context->>'runtime_action_id', 'run_id',prepared_task.delivery_context->>'runtime_run_id', 'readiness_revision',prepared_task.delivery_context->>'runtime_media_readiness_revision');
    END IF;
END IF;
readiness:=_agent_runtime_media_owner_readiness_v1();
IF (readiness->>'ready')::BOOLEAN IS NOT TRUE THEN
    RETURN jsonb_build_object('outcome','media_not_ready','runtime_owned',FALSE, 'readiness_revision',(readiness->>'state_version')::BIGINT);
END IF;
safe_arguments:=jsonb_strip_nulls(CASE WHEN p_tool_name='generate_image' THEN jsonb_build_object('prompt',p_arguments->'prompt','model',p_arguments->'model', 'aspect_ratio',p_arguments->'aspect_ratio', 'resolution',p_arguments->'resolution', 'output_format',p_arguments->'output_format') ELSE jsonb_build_object('prompt',p_arguments->'prompt','model',p_arguments->'model', 'aspect_ratio',p_arguments->'aspect_ratio', 'n_frames',p_arguments->'n_frames', 'remove_watermark',p_arguments->'remove_watermark') END);
IF NULLIF(btrim(safe_arguments->>'prompt'),'') IS NULL AND p_tool_name<>'generate_video' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
END IF;
session_result:=ensure_agent_runtime_session(p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id, p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision );
IF session_result->>'outcome' NOT IN ('created','already_exists') THEN RETURN session_result||jsonb_build_object('runtime_owned',FALSE);
END IF;
runtime_result:=submit_agent_runtime_chat_action_v1(p_conversation_id,p_org_id,p_user_id,p_task_id::TEXT, p_input_message_id::TEXT,p_task_id::TEXT,1,p_tool_name,safe_arguments, p_model_id,p_model_provider,p_model_revision,p_catalog_revision, p_policy_revision,'runtime_media_generation:'||p_tool_name,1, jsonb_build_object('source','media_ingress','task_id',p_task_id, 'input_message_id',p_input_message_id, 'output_message_id',p_output_message_id,'turn_id',p_turn_id, 'provider','kie','provider_revision','kie-runtime-media-v1', 'capability','media.provider.submit','capability_revision','v1', 'capability_requirements',jsonb_build_array('media.provider.submit')), jsonb_build_object('source','media_ingress','task_id',p_task_id, 'input_message_id',p_input_message_id, 'output_message_id',p_output_message_id,'turn_id',p_turn_id), p_idempotency_key );
IF runtime_result->>'outcome' IN ('created','already_exists') THEN UPDATE tasks SET delivery_context=delivery_context||jsonb_build_object('actor',FALSE,'runtime',TRUE,'runtime_owner','action_loop', 'runtime_action_id',runtime_result->>'action_id', 'runtime_run_id',runtime_result->>'run_id', 'runtime_media_readiness_revision',(readiness->>'state_version')::BIGINT) WHERE id=p_task_id;
SELECT * INTO runtime_action FROM agent_actions WHERE id=(runtime_result->>'action_id')::UUID;
SELECT * INTO runtime_session FROM agent_runtime_sessions WHERE id=runtime_action.session_id;
IF runtime_action.id IS NULL OR runtime_session.id IS NULL OR runtime_action.run_id IS DISTINCT FROM (runtime_result->>'run_id')::UUID THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_BINDING_INVALID' USING ERRCODE='55000';
END IF;
prepared_result:=_prepare_agent_runtime_prepared_media_binding_v1(
    jsonb_build_object('action_id',runtime_action.id,'session_id',runtime_session.id,
      'run_id',runtime_action.run_id,'model_step_id',runtime_action.model_step_id,
      'org_id',runtime_session.org_id,'user_id',runtime_session.user_id,
      'conversation_id',runtime_session.conversation_id,'tool_name',runtime_action.tool_name,
      'source','media_ingress','task_id',p_task_id,'input_message_id',p_input_message_id,
      'output_message_id',p_output_message_id),
    runtime_action.request_hash
);
IF prepared_result->>'outcome' NOT IN ('prepared','already_prepared') THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_BINDING_INVALID' USING ERRCODE='55000';
END IF;
RETURN runtime_result||jsonb_build_object('runtime_owned',TRUE, 'readiness_revision',(readiness->>'state_version')::BIGINT);
END IF;
RETURN runtime_result||jsonb_build_object('runtime_owned',FALSE);
END;
$$;
CREATE FUNCTION record_agent_runtime_media_provider_submission_v1(
    p_attempt_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_provider TEXT, p_provider_task_ref TEXT, p_status_locator TEXT,
    p_callback_correlation TEXT, p_provider_idempotency_key TEXT,
    p_provider_request_hash TEXT, p_next_reconcile_at TIMESTAMPTZ,
    p_external_receipt JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    attempt agent_action_attempts%ROWTYPE;
    result JSONB;
    tool_name TEXT;
    batch_provider_hash TEXT;
    prepared_provider_hash TEXT;
    expected_provider_hash TEXT;
    provider_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO attempt FROM agent_action_attempts
     WHERE id=p_attempt_id FOR UPDATE;
    IF attempt.id IS NULL THEN
        RETURN jsonb_build_object('outcome','not_found');
    END IF;
    IF attempt.execution_token IS DISTINCT FROM p_execution_token
       OR attempt.request_hash IS DISTINCT FROM p_request_hash
       OR attempt.status NOT IN ('dispatching','accepted','unknown') THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM agent_action_dispatch_intents intent
        JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
         WHERE intent.attempt_id=attempt.id
           AND intent.action_id=attempt.action_id
           AND intent.execution_token=p_execution_token
           AND intent.request_hash=p_request_hash
           AND receipt.action_id=attempt.action_id
           AND receipt.expires_at>clock_timestamp()
    ) THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    IF NULLIF(btrim(p_provider),'') IS NULL
       OR NULLIF(btrim(p_provider_task_ref),'') IS NULL
       OR NULLIF(btrim(p_provider_idempotency_key),'') IS NULL
       OR length(btrim(p_provider_idempotency_key))>300
       OR COALESCE(p_provider_request_hash,'') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_external_receipt,'{}'::JSONB)) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AGENT_PROVIDER_RECEIPT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT action.tool_name,binding.provider_request_hash,
           prepared.provider_request_hash
      INTO tool_name,batch_provider_hash,prepared_provider_hash
      FROM agent_actions action
      LEFT JOIN agent_runtime_media_action_bindings binding
        ON binding.action_id=action.id
      LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
        ON prepared.action_id=action.id
     WHERE action.id=attempt.action_id;
    SELECT binding.provider_request_canonical_hash
      INTO batch_provider_hash
      FROM agent_runtime_media_action_bindings binding
     WHERE binding.action_id=attempt.action_id;
    expected_provider_hash:=COALESCE(prepared_provider_hash,batch_provider_hash);
    SELECT * INTO provider_fact
      FROM agent_runtime_provider_submission_facts
     WHERE attempt_id=attempt.id FOR UPDATE;
    IF tool_name NOT IN ('generate_image','generate_video')
       OR expected_provider_hash IS NULL
       OR (prepared_provider_hash IS NOT NULL AND batch_provider_hash IS NOT NULL
           AND prepared_provider_hash IS DISTINCT FROM batch_provider_hash)
       OR p_provider_request_hash IS DISTINCT FROM expected_provider_hash
       OR btrim(p_provider_idempotency_key) !~ '^[0-9a-f]{64}$'
       OR p_external_receipt#>>'{evidence,provider_request_hash}' IS DISTINCT FROM p_provider_request_hash
       OR p_external_receipt#>>'{evidence,provider_idempotency_key}' IS DISTINCT FROM btrim(p_provider_idempotency_key)
       OR provider_fact.id IS NULL
       OR provider_fact.action_id IS DISTINCT FROM attempt.action_id
       OR provider_fact.run_id IS DISTINCT FROM attempt.run_id
       OR provider_fact.execution_token IS DISTINCT FROM attempt.execution_token
       OR provider_fact.request_hash IS DISTINCT FROM attempt.request_hash
       OR provider_fact.provider IS DISTINCT FROM btrim(p_provider)
       OR provider_fact.external_idempotency_key IS DISTINCT FROM btrim(p_provider_idempotency_key)
       OR provider_fact.provider_task_ref IS DISTINCT FROM btrim(p_provider_task_ref)
       OR provider_fact.state IS DISTINCT FROM 'submitted'
       OR p_external_receipt#>>'{evidence,submission_id}' IS DISTINCT FROM provider_fact.id::TEXT
       OR p_external_receipt#>>'{evidence,state_version}' IS DISTINCT FROM provider_fact.state_version::TEXT THEN
        RAISE EXCEPTION 'AGENT_PROVIDER_RECEIPT_INVALID' USING ERRCODE='22023';
    END IF;
    UPDATE agent_action_attempts SET
        status='accepted',dispatch_phase='accepted',provider=btrim(p_provider),
        provider_task_ref=btrim(p_provider_task_ref),
        provider_status_locator=NULLIF(btrim(p_status_locator),''),
        callback_correlation=NULLIF(btrim(p_callback_correlation),''),
        provider_idempotency_key=btrim(p_provider_idempotency_key),
        provider_request_hash=p_provider_request_hash,
        next_reconcile_at=p_next_reconcile_at,last_provider_status='accepted',
        external_receipt=p_external_receipt,accepted_at=clock_timestamp(),
        state_version=state_version+1,updated_at=clock_timestamp()
      WHERE id=p_attempt_id
      RETURNING to_jsonb(agent_action_attempts.*) INTO result;
    UPDATE agent_actions SET status='accepted',accepted_at=clock_timestamp(),
        state_version=state_version+1,updated_at=clock_timestamp()
      WHERE id=attempt.action_id AND status='running';
    PERFORM _agent_runtime_226_append_action_event(
        attempt.action_id,'action.provider.accepted',
        jsonb_build_object('provider',p_provider,
                           'provider_task_ref',p_provider_task_ref)
    );
    RETURN jsonb_build_object('outcome','accepted','attempt',result);
END;
$$;
CREATE FUNCTION record_agent_runtime_media_provider_rejected_v1(
    p_submission_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_expected_state_version BIGINT,p_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NOT _agent_runtime_provider_evidence_safe(p_evidence)
       OR COALESCE(p_evidence,'{}'::JSONB)='{}'::JSONB THEN
        RAISE EXCEPTION 'RUNTIME_MEDIA_PROVIDER_REJECTION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO fact FROM agent_runtime_provider_submission_facts
     WHERE id=p_submission_id FOR UPDATE;
    IF fact.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF fact.execution_token IS DISTINCT FROM p_execution_token
       OR fact.request_hash IS DISTINCT FROM p_request_hash
       OR fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF fact.provider IS DISTINCT FROM 'kie'
       OR fact.provider_revision IS DISTINCT FROM 'kie-runtime-media-v1'
       OR fact.state IS DISTINCT FROM 'submission_pending' THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET state='failed',
        ambiguity_evidence=p_evidence,next_reconcile_at=NULL,
        state_version=state_version+1,updated_at=clock_timestamp()
     WHERE id=fact.id RETURNING * INTO fact;
    RETURN jsonb_build_object('outcome','failed','submission_id',fact.id,
        'state',fact.state,'state_version',fact.state_version);
END;
$$;
CREATE FUNCTION record_agent_runtime_media_provider_unknown_v1(
    p_attempt_id UUID,p_execution_token UUID,p_expected_state_version BIGINT,
    p_request_hash TEXT,p_provider_receipt JSONB,p_ambiguity_evidence JSONB,
    p_next_reconcile_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; action agent_actions%ROWTYPE;
provider_fact agent_runtime_provider_submission_facts%ROWTYPE;
expected_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF jsonb_typeof(COALESCE(p_provider_receipt,'{}')) IS DISTINCT FROM 'object'
       OR jsonb_typeof(COALESCE(p_ambiguity_evidence,'{}')) IS DISTINCT FROM 'object'
       OR COALESCE(p_ambiguity_evidence,'{}')='{}'::JSONB
       OR p_provider_receipt->>'provider' IS DISTINCT FROM 'kie'
       OR COALESCE(p_provider_receipt#>>'{evidence,submission_id}','')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_provider_receipt#>>'{evidence,state_version}','') !~ '^[0-9]+$'
       OR p_next_reconcile_at IS NULL OR p_next_reconcile_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_UNKNOWN_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
    IF attempt.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
    SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT COALESCE(prepared.provider_request_hash,binding.provider_request_canonical_hash)
      INTO expected_hash FROM agent_actions candidate
      LEFT JOIN agent_runtime_media_action_bindings binding ON binding.action_id=candidate.id
      LEFT JOIN agent_runtime_prepared_media_action_bindings prepared ON prepared.action_id=candidate.id
     WHERE candidate.id=action.id;
    SELECT * INTO provider_fact FROM agent_runtime_provider_submission_facts
     WHERE id=(p_provider_receipt#>>'{evidence,submission_id}')::UUID FOR UPDATE;
    IF attempt.status IS DISTINCT FROM 'dispatching' OR action.status IS DISTINCT FROM 'running'
       OR attempt.execution_token IS DISTINCT FROM p_execution_token
       OR attempt.state_version IS DISTINCT FROM p_expected_state_version
       OR attempt.request_hash IS DISTINCT FROM p_request_hash
       OR expected_hash IS NULL
       OR expected_hash IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_request_hash}'
       OR provider_fact.id IS NULL OR provider_fact.attempt_id IS DISTINCT FROM attempt.id
       OR provider_fact.action_id IS DISTINCT FROM action.id
       OR provider_fact.run_id IS DISTINCT FROM attempt.run_id
       OR provider_fact.execution_token IS DISTINCT FROM attempt.execution_token
       OR provider_fact.request_hash IS DISTINCT FROM attempt.request_hash
       OR provider_fact.provider IS DISTINCT FROM 'kie'
       OR provider_fact.provider_revision IS DISTINCT FROM 'kie-runtime-media-v1'
       OR provider_fact.external_idempotency_key IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_idempotency_key}'
       OR provider_fact.state IS DISTINCT FROM 'unknown'
       OR provider_fact.state_version::TEXT IS DISTINCT FROM p_provider_receipt#>>'{evidence,state_version}'
       OR NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents intent
            WHERE intent.attempt_id=attempt.id AND intent.action_id=action.id
              AND intent.execution_token=p_execution_token
              AND intent.request_hash=p_request_hash) THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    UPDATE agent_action_attempts SET status='unknown',
        provider=CASE WHEN NULLIF(p_provider_receipt->>'provider_task_ref','') IS NULL THEN NULL ELSE 'kie' END,
        provider_task_ref=NULLIF(p_provider_receipt->>'provider_task_ref',''),
        provider_status_locator=NULLIF(p_provider_receipt->>'status_locator',''),
        provider_idempotency_key=p_provider_receipt#>>'{evidence,provider_idempotency_key}',
        provider_request_hash=expected_hash,external_receipt=p_provider_receipt,
        ambiguity_evidence=p_ambiguity_evidence,last_provider_status='unknown',
        next_reconcile_at=p_next_reconcile_at,state_version=state_version+1,
        updated_at=clock_timestamp() WHERE id=attempt.id;
    UPDATE agent_actions SET status='unknown',state_version=state_version+1,
        updated_at=clock_timestamp() WHERE id=action.id;
    PERFORM _agent_runtime_226_append_action_event(action.id,
        'action.provider.unknown',p_ambiguity_evidence);
    RETURN jsonb_build_object('outcome','unknown','attempt_id',attempt.id);
END;
$$;
CREATE FUNCTION finalize_agent_runtime_media_after_cancel_v1(
    p_attempt_id UUID,p_execution_token UUID,p_reconciliation_token UUID,
    p_expected_state_version INTEGER,p_request_hash TEXT,p_terminal_state TEXT,
    p_provider_receipt JSONB,p_result JSONB,p_cost_kind TEXT,
    p_reserved_amount BIGINT,p_actual_amount BIGINT,p_currency TEXT,
    p_reason_code TEXT,p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; action agent_actions%ROWTYPE;
runtime_run agent_runs%ROWTYPE; runtime_session agent_runtime_sessions%ROWTYPE;
provider_fact agent_runtime_provider_submission_facts%ROWTYPE;
existing agent_action_results%ROWTYPE; result_hash TEXT; cost_result JSONB; event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_execution_token IS NOT NULL OR p_terminal_state NOT IN ('completed','failed')
       OR jsonb_typeof(COALESCE(p_provider_receipt,'{}')) IS DISTINCT FROM 'object'
       OR jsonb_typeof(COALESCE(p_result,'{}')) IS DISTINCT FROM 'object'
       OR p_provider_receipt#>>'{evidence,cancel_unproven}' IS DISTINCT FROM 'true'
       OR COALESCE(p_provider_receipt#>>'{evidence,submission_id}','')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_provider_receipt#>>'{evidence,state_version}','') !~ '^[0-9]+$'
       OR COALESCE(p_provider_receipt_hash,'') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_READBACK_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
    IF attempt.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    SELECT * INTO runtime_session FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
    SELECT * INTO runtime_run FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
    SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT * INTO existing FROM agent_action_results WHERE action_id=action.id FOR UPDATE;
    SELECT * INTO provider_fact FROM agent_runtime_provider_submission_facts
     WHERE id=(p_provider_receipt#>>'{evidence,submission_id}')::UUID FOR UPDATE;
    result_hash:=_agent_action_result_hash(
        p_result,p_terminal_state,runtime_session.conversation_id,action.org_id
    );
    IF attempt.status IN ('completed','failed') AND action.status=attempt.status THEN
        IF attempt.status IS DISTINCT FROM p_terminal_state OR existing.action_id IS NULL
           OR existing.result_hash IS DISTINCT FROM result_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_READBACK_CONFLICT' USING ERRCODE='40001';
        END IF;
        RETURN jsonb_build_object('outcome','already_'||p_terminal_state,'action_id',action.id);
    END IF;
    IF runtime_run.status IS DISTINCT FROM 'cancelled'
       OR attempt.status NOT IN ('accepted','unknown')
       OR action.status NOT IN ('accepted','unknown')
       OR attempt.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR attempt.reconciliation_lease_expires_at<=clock_timestamp()
       OR attempt.state_version IS DISTINCT FROM p_expected_state_version
       OR attempt.request_hash IS DISTINCT FROM p_request_hash
       OR attempt.provider IS DISTINCT FROM 'kie'
       OR attempt.provider_task_ref IS DISTINCT FROM p_provider_receipt->>'provider_task_ref'
       OR attempt.provider_request_hash IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_request_hash}'
       OR attempt.provider_idempotency_key IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_idempotency_key}'
       OR attempt.external_receipt#>>'{evidence,cancel_unproven}' IS DISTINCT FROM 'true'
       OR provider_fact.id IS NULL OR provider_fact.attempt_id IS DISTINCT FROM attempt.id
       OR provider_fact.action_id IS DISTINCT FROM action.id
       OR provider_fact.run_id IS DISTINCT FROM runtime_run.id
       OR provider_fact.execution_token IS DISTINCT FROM attempt.execution_token
       OR provider_fact.request_hash IS DISTINCT FROM attempt.request_hash
       OR provider_fact.provider IS DISTINCT FROM 'kie'
       OR provider_fact.provider_revision IS DISTINCT FROM 'kie-runtime-media-v1'
       OR provider_fact.provider_task_ref IS DISTINCT FROM attempt.provider_task_ref
       OR provider_fact.external_idempotency_key IS DISTINCT FROM attempt.provider_idempotency_key
       OR provider_fact.cancel_requested_at IS NULL
       OR provider_fact.state_version::TEXT IS DISTINCT FROM p_provider_receipt#>>'{evidence,state_version}'
       OR provider_fact.state IS DISTINCT FROM (CASE p_terminal_state
            WHEN 'completed' THEN 'readback_confirmed' ELSE 'failed' END) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_READBACK_FENCED' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents intent
        JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
        WHERE intent.attempt_id=attempt.id AND intent.action_id=action.id
          AND intent.execution_token=attempt.execution_token
          AND intent.request_hash=p_request_hash AND receipt.action_id=action.id
          AND receipt.decision='allow') THEN
        RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501';
    END IF;
    IF p_cost_kind IS NOT NULL THEN
        SELECT record_agent_action_cost_strict(action.id,attempt.id,p_cost_kind,
            p_reserved_amount,p_actual_amount,p_currency,p_reason_code,
            p_provider_receipt_hash) INTO cost_result;
    END IF;
    INSERT INTO agent_action_results(action_id,session_id,run_id,org_id,user_id,
        status,result_hash,summary,data,artifact_ids,usage,cost,external_receipt,error_code)
    VALUES(action.id,action.session_id,action.run_id,action.org_id,action.user_id,
        p_result->>'status',result_hash,COALESCE(p_result->>'summary',''),p_result->'data',
        ARRAY(SELECT value::UUID FROM jsonb_array_elements_text(
            COALESCE(p_result->'artifact_ids','[]'::JSONB)) value),
        COALESCE(p_result->'usage','{}'),COALESCE(p_result->'cost','{}'),
        p_provider_receipt,p_result->>'error_code');
    UPDATE agent_action_attempts SET status=p_terminal_state,
        external_receipt=p_provider_receipt,last_provider_status=p_terminal_state,
        ended_at=clock_timestamp(),reconciliation_token=NULL,
        reconciliation_lease_expires_at=NULL,next_reconcile_at=NULL,
        state_version=state_version+1,updated_at=clock_timestamp() WHERE id=attempt.id;
    UPDATE agent_actions SET status=p_terminal_state,
        terminal_reason='provider_'||p_terminal_state||'_after_cancel',
        completed_at=clock_timestamp(),state_version=state_version+1,
        updated_at=clock_timestamp() WHERE id=action.id;
    event:=append_agent_runtime_event(action.session_id,
        'action.'||p_terminal_state||'_after_cancel',action.run_id,
        action.model_step_id,action.id,'reconciler',session_user,
        jsonb_build_object('action_id',action.id,'result_hash',result_hash,
            'cancel_unproven',TRUE),ARRAY['web_runtime','audit']::TEXT[]);
    RETURN jsonb_build_object('outcome',p_terminal_state,'action_id',action.id,
        'result_hash',result_hash,'run_status',runtime_run.status,
        'cost',COALESCE(cost_result,'{}'),'event_sequence',event->'event_sequence');
END;
$$;
CREATE FUNCTION record_agent_runtime_media_cancel_unproven_v1(
    p_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version BIGINT,
    p_request_hash TEXT,p_provider_receipt JSONB,p_ambiguity_evidence JSONB,
    p_next_reconcile_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; action agent_actions%ROWTYPE;
provider_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF jsonb_typeof(COALESCE(p_provider_receipt,'{}')) IS DISTINCT FROM 'object'
       OR jsonb_typeof(COALESCE(p_ambiguity_evidence,'{}')) IS DISTINCT FROM 'object'
       OR COALESCE(p_ambiguity_evidence,'{}')='{}'::JSONB
       OR p_provider_receipt->>'provider' IS DISTINCT FROM 'kie'
       OR p_provider_receipt#>>'{evidence,error_code}' IS DISTINCT FROM 'CANCEL_UNPROVEN'
       OR p_provider_receipt#>>'{evidence,cancel_unproven}' IS DISTINCT FROM 'true'
       OR COALESCE(p_provider_receipt#>>'{evidence,submission_id}','')
            !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_provider_receipt#>>'{evidence,state_version}','') !~ '^[0-9]+$'
       OR p_next_reconcile_at IS NULL OR p_next_reconcile_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_UNPROVEN_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
    IF attempt.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
    PERFORM 1 FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
    SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
    SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    SELECT * INTO provider_fact FROM agent_runtime_provider_submission_facts
     WHERE id=(p_provider_receipt#>>'{evidence,submission_id}')::UUID FOR UPDATE;
    IF attempt.status NOT IN ('accepted','unknown') OR action.status<>attempt.status
       OR attempt.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR attempt.reconciliation_lease_expires_at<=clock_timestamp()
       OR attempt.state_version IS DISTINCT FROM p_expected_state_version
       OR attempt.request_hash IS DISTINCT FROM p_request_hash
       OR NOT (
            (attempt.provider='kie' AND attempt.provider_task_ref IS NOT DISTINCT FROM p_provider_receipt->>'provider_task_ref')
            OR (attempt.provider IS NULL AND attempt.provider_task_ref IS NULL
                AND NULLIF(p_provider_receipt->>'provider_task_ref','') IS NULL)
       )
       OR attempt.provider_request_hash IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_request_hash}'
       OR attempt.provider_idempotency_key IS DISTINCT FROM p_provider_receipt#>>'{evidence,provider_idempotency_key}'
       OR provider_fact.id IS NULL OR provider_fact.attempt_id IS DISTINCT FROM attempt.id
       OR provider_fact.action_id IS DISTINCT FROM action.id
       OR provider_fact.execution_token IS DISTINCT FROM attempt.execution_token
       OR provider_fact.request_hash IS DISTINCT FROM attempt.request_hash
       OR provider_fact.provider IS DISTINCT FROM 'kie'
       OR provider_fact.provider_revision IS DISTINCT FROM 'kie-runtime-media-v1'
       OR provider_fact.provider_task_ref IS DISTINCT FROM attempt.provider_task_ref
       OR provider_fact.external_idempotency_key IS DISTINCT FROM attempt.provider_idempotency_key
       OR provider_fact.state IS DISTINCT FROM 'cancel_requested'
       OR provider_fact.state_version::TEXT IS DISTINCT FROM p_provider_receipt#>>'{evidence,state_version}' THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    UPDATE agent_action_attempts SET status='unknown',
        external_receipt=p_provider_receipt,ambiguity_evidence=p_ambiguity_evidence,
        last_provider_status='unknown',next_reconcile_at=p_next_reconcile_at,
        reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,
        state_version=state_version+1,updated_at=clock_timestamp() WHERE id=attempt.id;
    UPDATE agent_actions SET status='unknown',state_version=state_version+1,
        updated_at=clock_timestamp() WHERE id=action.id AND status IN ('accepted','unknown');
    PERFORM _agent_runtime_226_append_action_event(action.id,
        'action.provider.cancel_unproven',jsonb_build_object(
            'provider','kie','provider_task_ref',attempt.provider_task_ref));
    RETURN jsonb_build_object('outcome','still_unknown','attempt_id',attempt.id);
END;
$$;
CREATE FUNCTION _prepare_agent_runtime_prepared_media_binding_v1(p_context JSONB,p_request_hash TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE action agent_actions%ROWTYPE;
prepared_task tasks%ROWTYPE;
input_message messages%ROWTYPE;
output_message messages%ROWTYPE;
binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
image_price agent_runtime_media_pricing_facts%ROWTYPE;
video_price agent_runtime_prepared_media_video_pricing_facts%ROWTYPE;
resolved JSONB;
urls JSONB;
provider_request JSONB;
provider_hash TEXT;
task_hash TEXT;
kind TEXT;
selected_model TEXT;
pricing_revision TEXT;
pricing_key TEXT;
pricing_fact_hash TEXT;
unit_credits INTEGER;
selected_duration_seconds INTEGER;
selected_resolution_key TEXT;
transaction_id UUID;
final_balance INTEGER;
BEGIN SELECT * INTO action FROM agent_actions WHERE id=(p_context->>'action_id')::UUID;
SELECT * INTO prepared_task FROM tasks WHERE id=NULLIF(p_context->>'task_id','')::UUID FOR UPDATE;
SELECT * INTO input_message FROM messages WHERE id=NULLIF(p_context->>'input_message_id','')::UUID;
SELECT * INTO output_message FROM messages WHERE id=NULLIF(p_context->>'output_message_id','')::UUID;
kind:=CASE action.tool_name WHEN 'generate_image' THEN 'image' ELSE 'video' END;
IF p_context->>'source'<>'media_ingress' OR prepared_task.id IS NULL OR input_message.id IS NULL OR output_message.id IS NULL OR prepared_task.type::TEXT<>kind OR prepared_task.user_id IS DISTINCT FROM (p_context->>'user_id')::UUID OR prepared_task.org_id IS DISTINCT FROM NULLIF(p_context->>'org_id','')::UUID OR prepared_task.conversation_id IS DISTINCT FROM (p_context->>'conversation_id')::UUID OR prepared_task.input_message_id IS DISTINCT FROM input_message.id OR prepared_task.assistant_message_id IS DISTINCT FROM output_message.id OR input_message.conversation_id IS DISTINCT FROM prepared_task.conversation_id OR output_message.conversation_id IS DISTINCT FROM prepared_task.conversation_id OR input_message.org_id IS DISTINCT FROM prepared_task.org_id OR output_message.org_id IS DISTINCT FROM prepared_task.org_id OR output_message.generation_params->>'type'='image_ecom' OR prepared_task.delivery_context @> jsonb_build_object('actor',FALSE,'runtime',TRUE, 'runtime_action_id',action.id::TEXT) IS NOT TRUE OR action.arguments ?| ARRAY[ 'task_id','user_id','org_id','credit_transaction_id', 'reserved_credits','currency','image_urls','input_urls','runtime_task' ] THEN RAISE EXCEPTION 'AGENT_RUNTIME_PREPARED_MEDIA_SCOPE_INVALID' USING ERRCODE='42501';
END IF;
resolved:=_agent_runtime_media_resolved_images_v1(action.session_id,input_message.id );
urls:=(SELECT COALESCE(jsonb_agg(image->'url' ORDER BY (image->>'index')::INTEGER),'[]'::JSONB) FROM jsonb_array_elements(resolved->'images') image);
provider_request:=_agent_runtime_kie_provider_request_v1(kind,prepared_task.request_params,urls );
provider_hash:=encode(digest(convert_to(provider_request::TEXT,'UTF8'),'sha256'),'hex');
task_hash:=encode(digest(convert_to(prepared_task.request_params::TEXT,'UTF8'),'sha256'),'hex');
selected_model:=provider_request->>'model';
IF kind='image' THEN selected_resolution_key:=COALESCE(NULLIF(prepared_task.request_params->>'resolution',''),'1K');
IF selected_resolution_key NOT IN ('1K','2K','4K') THEN selected_resolution_key:='1K';
END IF;
SELECT * INTO image_price FROM agent_runtime_media_pricing_facts price WHERE price.pricing_revision='kie-image-pricing-v1' AND price.model_id=selected_model AND price.active AND price.resolution_key=CASE WHEN price.supports_resolution THEN selected_resolution_key ELSE 'default' END;
IF image_price.model_id IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PRICING_UNAVAILABLE' USING ERRCODE='22023';
END IF;
pricing_revision:=image_price.pricing_revision;
pricing_key:=image_price.resolution_key;
pricing_fact_hash:=image_price.fact_hash;
unit_credits:=image_price.user_credits;
ELSE selected_duration_seconds:=(provider_request#>>'{input,n_frames}')::INTEGER;
SELECT * INTO video_price FROM agent_runtime_prepared_media_video_pricing_facts price WHERE price.pricing_revision='kie-video-pricing-v1' AND price.model_id=selected_model AND price.duration_seconds=selected_duration_seconds AND price.active;
IF video_price.model_id IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PRICING_UNAVAILABLE' USING ERRCODE='22023';
END IF;
pricing_revision:=video_price.pricing_revision;
pricing_key:=selected_duration_seconds::TEXT;
pricing_fact_hash:=video_price.fact_hash;
unit_credits:=video_price.user_credits;
END IF;
SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings WHERE action_id=action.id;
IF binding.action_id IS NOT NULL THEN IF binding.task_id IS DISTINCT FROM prepared_task.id OR binding.session_id IS DISTINCT FROM action.session_id OR binding.run_id IS DISTINCT FROM action.run_id OR binding.model_step_id IS DISTINCT FROM action.model_step_id OR binding.org_id IS DISTINCT FROM prepared_task.org_id OR binding.user_id IS DISTINCT FROM prepared_task.user_id OR binding.conversation_id IS DISTINCT FROM prepared_task.conversation_id OR binding.input_message_id IS DISTINCT FROM input_message.id OR binding.output_message_id IS DISTINCT FROM output_message.id OR binding.media_kind IS DISTINCT FROM kind OR binding.action_request_hash IS DISTINCT FROM p_request_hash OR binding.task_request_hash IS DISTINCT FROM task_hash OR binding.reference_manifest_hash IS DISTINCT FROM resolved->>'manifest_hash' OR binding.provider_request_hash IS DISTINCT FROM provider_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_PREPARED_MEDIA_BINDING_CONFLICT' USING ERRCODE='23505';
END IF;
RETURN jsonb_build_object('outcome','already_prepared');
END IF;
IF prepared_task.credit_transaction_id IS NOT NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_PREPARED_MEDIA_CREDIT_CONFLICT' USING ERRCODE='23505';
END IF;
UPDATE users SET credits=credits-unit_credits,updated_at=clock_timestamp() WHERE id=prepared_task.user_id AND status::TEXT='active' AND credits>=unit_credits RETURNING credits INTO final_balance;
IF final_balance IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INSUFFICIENT_CREDITS' USING ERRCODE='P0001';
END IF;
transaction_id:=gen_random_uuid();
INSERT INTO credit_transactions(id,task_id,user_id,amount,type,status,reason,org_id) VALUES(transaction_id,prepared_task.id,prepared_task.user_id,unit_credits, 'lock','pending','Agent Runtime prepared media reservation',prepared_task.org_id);
INSERT INTO credits_history(user_id,change_type,change_amount,balance_after,description,org_id ) VALUES(prepared_task.user_id,'image_generation_cost'::credits_change_type, -unit_credits,final_balance,'Agent Runtime prepared media reservation', prepared_task.org_id );
UPDATE tasks SET credits_locked=unit_credits,credit_transaction_id=transaction_id, delivery_context=delivery_context||jsonb_build_object('actor',FALSE,'runtime',TRUE,'runtime_owner','action_loop') WHERE id=prepared_task.id;
INSERT INTO agent_runtime_prepared_media_action_bindings(action_id,task_id,session_id,run_id,model_step_id,org_id,user_id, conversation_id,input_message_id,output_message_id,media_kind, action_request_hash,task_request_hash,reference_manifest_hash, provider_request_hash,pricing_revision,pricing_model_id,pricing_key, pricing_fact_hash,unit_credits,credit_transaction_id ) VALUES(action.id,prepared_task.id,action.session_id,action.run_id,action.model_step_id, prepared_task.org_id,prepared_task.user_id,prepared_task.conversation_id, input_message.id,output_message.id,kind,p_request_hash,task_hash, resolved->>'manifest_hash',provider_hash,pricing_revision,selected_model, pricing_key,pricing_fact_hash,unit_credits,transaction_id );
IF kind='image' THEN
INSERT INTO agent_runtime_media_action_bindings(
  action_id,task_id,session_id,run_id,model_step_id,chat_task_id,org_id,user_id,
  conversation_id,batch_hash,action_index,action_arguments_hash,
  action_request_hash,input_message_id,output_message_id,credit_transaction_id,
  pricing_revision,pricing_model_id,pricing_resolution,pricing_fact_hash,
  provider_request_hash,unit_credits,reference_manifest_hash,
  provider_request_canonical_hash
) VALUES(
  action.id,prepared_task.id,action.session_id,action.run_id,action.model_step_id,
  prepared_task.id,prepared_task.org_id,prepared_task.user_id,
  prepared_task.conversation_id,action.batch_hash,
  COALESCE(prepared_task.image_index,0),action.arguments_hash,p_request_hash,
  input_message.id,output_message.id,transaction_id,pricing_revision,
  selected_model,pricing_key,pricing_fact_hash,provider_hash,unit_credits,
  resolved->>'manifest_hash',provider_hash
);
END IF;
UPDATE messages SET
  content=(SELECT COALESCE(jsonb_agg(part ORDER BY ordinality)
             FILTER (WHERE part->>'slot_id' IS DISTINCT FROM action.id::TEXT),
             '[]'::JSONB)
             FROM jsonb_array_elements(output_message.content::JSONB)
             WITH ORDINALITY source(part,ordinality))
          ||jsonb_build_array(jsonb_build_object(
               'type',kind,'url',NULL,'slot_id',action.id,
               'slot_index',COALESCE(prepared_task.image_index,0),
               'slot_status','pending','slot_revision',0)),
  status='pending',
  generation_params=COALESCE(generation_params,'{}'::JSONB)
    ||jsonb_build_object('runtime_media_prepared',TRUE)
 WHERE id=output_message.id;
RETURN jsonb_build_object('outcome','prepared');
END;
$$;
CREATE FUNCTION prepare_agent_runtime_media_dispatch_v1(p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID, p_expected_attempt_version BIGINT,p_request_hash TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE context JSONB;
manifest JSONB;
prepared JSONB;
request_fact JSONB;
BEGIN context:=_agent_runtime_media_attempt_context_v2(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
IF context->>'source'='media_ingress' THEN RETURN _prepare_agent_runtime_prepared_media_binding_v1(context,p_request_hash);
END IF;
IF context->>'tool_name'<>'generate_image' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BATCH_KIND_INVALID' USING ERRCODE='22023';
END IF;
manifest:=read_agent_runtime_media_manifest_v1(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
prepared:=prepare_agent_runtime_media_batch_v1(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash,manifest->>'reference_manifest_hash');
request_fact:=read_agent_runtime_media_provider_request_v1(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
UPDATE agent_runtime_media_action_bindings SET
    provider_request_canonical_hash=request_fact->>'provider_request_hash',
    updated_at=clock_timestamp()
 WHERE action_id=p_action_id
   AND (provider_request_canonical_hash IS NULL OR provider_request_canonical_hash=request_fact->>'provider_request_hash');
IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT' USING ERRCODE='23505';
END IF;
RETURN jsonb_build_object('outcome',prepared->>'outcome');
END;
$$;
CREATE FUNCTION read_agent_runtime_media_provider_request_v1(p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID, p_expected_attempt_version BIGINT,p_request_hash TEXT ) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE context JSONB;
action agent_actions%ROWTYPE;
task tasks%ROWTYPE;
batch_binding agent_runtime_media_action_bindings%ROWTYPE;
prepared_binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
resolved JSONB;
selected_urls JSONB;
indexes JSONB;
provider_request JSONB;
provider_hash TEXT;
kind TEXT;
BEGIN context:=_agent_runtime_media_attempt_context_v2(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
SELECT * INTO action FROM agent_actions WHERE id=p_action_id;
kind:=CASE action.tool_name WHEN 'generate_image' THEN 'image' ELSE 'video' END;
IF context->>'source'='media_ingress' THEN SELECT * INTO prepared_binding FROM agent_runtime_prepared_media_action_bindings WHERE action_id=action.id;
SELECT * INTO task FROM tasks WHERE id=prepared_binding.task_id;
IF prepared_binding.action_id IS NULL OR task.id IS NULL OR task.user_id IS DISTINCT FROM prepared_binding.user_id OR task.org_id IS DISTINCT FROM prepared_binding.org_id OR task.conversation_id IS DISTINCT FROM prepared_binding.conversation_id OR task.input_message_id IS DISTINCT FROM prepared_binding.input_message_id OR task.assistant_message_id IS DISTINCT FROM prepared_binding.output_message_id OR task.credit_transaction_id IS DISTINCT FROM prepared_binding.credit_transaction_id OR encode(digest(convert_to(task.request_params::TEXT,'UTF8'),'sha256'),'hex') IS DISTINCT FROM prepared_binding.task_request_hash OR task.delivery_context @> jsonb_build_object('actor',FALSE,'runtime',TRUE,'runtime_action_id',action.id::TEXT) IS NOT TRUE THEN RAISE EXCEPTION 'AGENT_RUNTIME_PREPARED_MEDIA_BINDING_CONFLICT' USING ERRCODE='23505';
END IF;
resolved:=_agent_runtime_media_resolved_images_v1(action.session_id,prepared_binding.input_message_id);
IF resolved->>'manifest_hash' IS DISTINCT FROM prepared_binding.reference_manifest_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_CONFLICT' USING ERRCODE='23505';
END IF;
selected_urls:=(SELECT COALESCE(jsonb_agg(image->'url' ORDER BY (image->>'index')::INTEGER),'[]'::JSONB) FROM jsonb_array_elements(resolved->'images') image);
ELSE SELECT * INTO batch_binding FROM agent_runtime_media_action_bindings WHERE action_id=action.id;
SELECT * INTO task FROM tasks WHERE id=batch_binding.task_id;
IF kind<>'image' OR batch_binding.action_id IS NULL OR task.id IS NULL OR task.user_id IS DISTINCT FROM batch_binding.user_id OR task.org_id IS DISTINCT FROM batch_binding.org_id OR task.conversation_id IS DISTINCT FROM batch_binding.conversation_id OR task.input_message_id IS DISTINCT FROM batch_binding.input_message_id OR task.assistant_message_id IS DISTINCT FROM batch_binding.output_message_id OR encode(digest(convert_to(task.request_params::TEXT,'UTF8'),'sha256'),'hex') IS DISTINCT FROM batch_binding.provider_request_hash OR task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB IS NOT TRUE THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDING_CONFLICT' USING ERRCODE='23505';
END IF;
resolved:=_agent_runtime_media_resolved_images_v1(action.session_id,batch_binding.input_message_id);
IF resolved->>'manifest_hash' IS DISTINCT FROM batch_binding.reference_manifest_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_CONFLICT' USING ERRCODE='23505';
END IF;
indexes:=COALESCE(action.arguments->'reference_image_indexes','[]'::JSONB);
IF jsonb_typeof(indexes)<>'array' OR EXISTS (SELECT 1 FROM jsonb_array_elements(indexes) index_value WHERE jsonb_typeof(index_value)<>'number' OR index_value::TEXT !~ '^(0|[1-9][0-9]*)$' OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(resolved->'images') image WHERE (image->>'index')::INTEGER=(index_value::TEXT)::INTEGER) ) OR (SELECT count(*) FROM jsonb_array_elements(indexes))<> (SELECT count(DISTINCT value::TEXT) FROM jsonb_array_elements(indexes)) THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_REFERENCE_INDEX_INVALID' USING ERRCODE='22023';
END IF;
selected_urls:=(SELECT COALESCE(jsonb_agg(found.candidate->'url' ORDER BY selected.ordinality ),'[]'::JSONB) FROM jsonb_array_elements(indexes) WITH ORDINALITY selected(value,ordinality) JOIN LATERAL (SELECT candidate FROM jsonb_array_elements(resolved->'images') candidate WHERE (candidate->>'index')::INTEGER=(selected.value::TEXT)::INTEGER ) found ON TRUE);
END IF;
provider_request:=_agent_runtime_kie_provider_request_v1(kind,task.request_params,selected_urls);
provider_hash:=encode(digest(convert_to(provider_request::TEXT,'UTF8'),'sha256'),'hex');
IF prepared_binding.action_id IS NOT NULL AND provider_hash IS DISTINCT FROM prepared_binding.provider_request_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT' USING ERRCODE='23505';
END IF;
IF batch_binding.action_id IS NOT NULL AND batch_binding.provider_request_canonical_hash IS NOT NULL AND provider_hash IS DISTINCT FROM batch_binding.provider_request_canonical_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT' USING ERRCODE='23505';
END IF;
RETURN jsonb_build_object('outcome','found','source',context->>'source','kind',kind, 'provider_request',provider_request,'provider_request_hash',provider_hash);
END;
$$;
CREATE FUNCTION get_agent_runtime_media_configuration_v1(p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID, p_expected_attempt_version BIGINT,p_request_hash TEXT, p_provider_request_hash TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE context JSONB;
request_fact JSONB;
BEGIN IF COALESCE(p_provider_request_hash,'') !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CONFIGURATION_INVALID' USING ERRCODE='22023';
END IF;
context:=_agent_runtime_media_attempt_context_v2(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
request_fact:=read_agent_runtime_media_provider_request_v1(p_action_id,p_attempt_id,p_worker_id,p_owner_token, p_expected_attempt_version,p_request_hash);
IF request_fact->>'provider_request_hash' IS DISTINCT FROM p_provider_request_hash THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT' USING ERRCODE='42501';
END IF;
RETURN _resolve_configuration_bundle('v1','ai.provider.kie',(context->>'user_id')::UUID, NULLIF(context->>'org_id','')::UUID);
END;
$$;
CREATE OR REPLACE FUNCTION worker_discover_media_tasks(p_limit INTEGER DEFAULT 100) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE discovered JSONB;
BEGIN IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501';
END IF;
IF p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID' USING ERRCODE='22023';
END IF;
SELECT COALESCE(jsonb_agg(to_jsonb(task_row)),'[]'::JSONB) INTO discovered FROM (SELECT task.* FROM tasks task WHERE task.status IN ('pending','running') AND task.type IN ('image','video') AND NOT EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding WHERE binding.task_id=task.id) AND NOT EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding WHERE binding.task_id=task.id) AND (task.org_id IS NULL OR EXISTS (SELECT 1 FROM organizations organization WHERE organization.id=task.org_id AND organization.status='active')) ORDER BY COALESCE(task.last_polled_at,task.created_at),task.id LIMIT p_limit ) task_row;
RETURN discovered;
END;
$$;
REVOKE ALL ON TABLE agent_runtime_prepared_media_video_pricing_facts, agent_runtime_prepared_media_action_bindings, agent_runtime_media_owner_readiness FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker, everydayai_sync,everydayai,everydayai_agent_runtime_worker, everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION _agent_runtime_media_owner_readiness_v1() FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION record_agent_runtime_media_projection_readiness_v1(TEXT,TEXT,BOOLEAN,INTEGER) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION record_agent_runtime_media_provider_submission_v1(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION record_agent_runtime_media_provider_rejected_v1(UUID,UUID,TEXT,BIGINT,JSONB) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION record_agent_runtime_media_provider_unknown_v1(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION finalize_agent_runtime_media_after_cancel_v1(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION record_agent_runtime_media_cancel_unproven_v1(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION _agent_runtime_media_attempt_context_v2(UUID,UUID,TEXT,UUID,BIGINT,TEXT), _agent_runtime_media_resolved_images_v1(UUID,UUID), _agent_runtime_kie_provider_request_v1(TEXT,JSONB,JSONB), _prepare_agent_runtime_prepared_media_binding_v1(JSONB,TEXT), prepare_agent_runtime_media_dispatch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT), read_agent_runtime_media_provider_request_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT), get_agent_runtime_media_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker, everydayai_sync,everydayai,everydayai_agent_runtime_worker, everydayai_projection_worker,everydayai_authorization_worker, everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION prepare_agent_runtime_media_dispatch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT), read_agent_runtime_media_provider_request_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT), get_agent_runtime_media_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_media_provider_submission_v1(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_media_provider_rejected_v1(UUID,UUID,TEXT,BIGINT,JSONB) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_media_provider_unknown_v1(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION finalize_agent_runtime_media_after_cancel_v1(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_media_cancel_unproven_v1(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION record_agent_runtime_media_projection_readiness_v1(TEXT,TEXT,BOOLEAN,INTEGER) TO everydayai_projection_worker;
REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime, everydayai_agent_runtime_worker,everydayai_projection_worker, everydayai_authorization_worker,everydayai_sandbox_worker, everydayai_sync,everydayai,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION worker_discover_media_tasks(INTEGER) TO everydayai_worker;
RESET ROLE;
