-- 225: Agent Runtime read-only capability boundary.
-- No business tables are created or granted to the worker.  Every public
-- function validates the persisted Action/Attempt/Run/Session/Dispatch facts.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_runtime_read_context(
    p_action_id UUID, p_attempt_id UUID, p_execution_token UUID,
    p_request_hash TEXT, p_executor_type TEXT, p_executor_revision INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_actions%ROWTYPE; t agent_action_attempts%ROWTYPE;
        r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
        i agent_action_dispatch_intents%ROWTYPE; cmd agent_session_commands%ROWTYPE;
        anchor messages%ROWTYPE; fence BIGINT;
BEGIN
    IF session_user <> 'everydayai_agent_runtime_worker'
       OR current_setting('app.access_kind', TRUE) <> 'agent_runtime' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_WORKER_REQUIRED' USING ERRCODE='42501';
    END IF;
    SELECT action.* INTO a FROM agent_actions action
     JOIN agent_action_attempts attempt ON attempt.id=p_attempt_id
       AND attempt.action_id=action.id
     JOIN agent_runs run ON run.id=action.run_id AND run.id=attempt.run_id
     JOIN agent_runtime_sessions session ON session.id=action.session_id
       AND session.id=attempt.session_id AND session.id=run.session_id
     JOIN agent_action_dispatch_intents intent ON intent.attempt_id=attempt.id
       AND intent.action_id=action.id
     WHERE action.id=p_action_id AND action.request_hash=p_request_hash
       AND attempt.execution_token=p_execution_token
       AND intent.execution_token=p_execution_token AND intent.request_hash=p_request_hash
       AND intent.executor_type=p_executor_type AND intent.executor_revision=p_executor_revision;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_BINDING_INVALID' USING ERRCODE='42501';
    END IF;
    SELECT * INTO t FROM agent_action_attempts WHERE id=p_attempt_id;
    SELECT * INTO r FROM agent_runs WHERE id=a.run_id;
    SELECT * INTO s FROM agent_runtime_sessions WHERE id=a.session_id;
    SELECT * INTO i FROM agent_action_dispatch_intents WHERE attempt_id=p_attempt_id;
    SELECT * INTO cmd FROM agent_session_commands WHERE id=r.command_id;
    IF a.status NOT IN ('queued','running') OR t.status NOT IN ('claimed','dispatching')
       OR r.status IN ('completed','failed','cancelled')
       OR t.lease_expires_at IS NULL OR t.lease_expires_at <= clock_timestamp()
       OR i.recovery_mode <> 'idempotent_replay' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_ATTEMPT_NOT_ACTIVE' USING ERRCODE='55000';
    END IF;
    IF a.org_id IS DISTINCT FROM t.org_id OR a.user_id IS DISTINCT FROM t.user_id
       OR a.org_id IS DISTINCT FROM r.org_id OR a.user_id IS DISTINCT FROM r.user_id
       OR s.org_id IS DISTINCT FROM a.org_id OR s.user_id IS DISTINCT FROM a.user_id
       OR s.conversation_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_SCOPE_INVALID' USING ERRCODE='42501';
    END IF;
    IF r.context_receipt->>'through_message_id' IS NULL
       OR r.context_receipt->>'base_context_revision' IS DISTINCT FROM
          ('message:' || (r.context_receipt->>'through_message_id'))
       OR r.context_receipt->>'session_id' IS DISTINCT FROM s.id::TEXT
       OR r.context_receipt->>'conversation_id' IS DISTINCT FROM s.conversation_id::TEXT
       OR r.config_snapshot IS DISTINCT FROM cmd.payload->'run_envelope'->'config_snapshot'
       OR r.capability_snapshot IS DISTINCT FROM cmd.payload->'run_envelope'->'capability_snapshot'
       OR cmd.payload->'run_envelope'->>'schema_revision' IS DISTINCT FROM '2'
       OR cmd.payload->'run_envelope'->'context_receipt' IS DISTINCT FROM r.context_receipt
       OR r.config_snapshot->>'base_context_revision' IS DISTINCT FROM r.context_receipt->>'base_context_revision'
       OR r.config_snapshot->>'through_message_id' IS DISTINCT FROM r.context_receipt->>'through_message_id'
       OR cmd.payload->>'release_revision' IS DISTINCT FROM r.config_snapshot->>'release_revision' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_CONTEXT_INVALID' USING ERRCODE='42501';
    END IF;
    SELECT m.* INTO anchor FROM messages m
      JOIN conversations v ON v.id=m.conversation_id
     WHERE m.id=(r.context_receipt->>'through_message_id')::UUID
       AND m.conversation_id=s.conversation_id
       AND m.org_id IS NOT DISTINCT FROM s.org_id
       AND ((s.scope_kind='user' AND v.user_id=s.user_id)
         OR (s.scope_kind='channel' AND v.scope_type='channel'
             AND v.scope_id=s.scope_id));
    IF NOT FOUND OR anchor.context_revision IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_ANCHOR_INVALID' USING ERRCODE='42501';
    END IF;
    fence:=anchor.context_revision;
    RETURN jsonb_build_object(
      'conversation_id', s.conversation_id, 'org_id', a.org_id,
      'user_id', a.user_id, 'scope_kind', s.scope_kind,
      'scope_id', s.scope_id, 'context_revision', fence,
      'through_message_id', anchor.id);
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_conversation(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_limit INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_limit IS NULL OR p_limit<1 OR p_limit>20 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_LIMIT_INVALID'; END IF;
 SELECT COALESCE(jsonb_agg(x), '[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('message_id',m.id,'role',m.role::TEXT,
     'text',left(m.content,1000),'created_at',m.created_at)
     x FROM messages m JOIN conversations v ON v.id=m.conversation_id
    WHERE m.conversation_id=(c->>'conversation_id')::UUID
      AND m.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID
      AND m.context_revision <= (c->>'context_revision')::BIGINT
    ORDER BY m.created_at DESC LIMIT p_limit) q;
 RETURN jsonb_build_object('summary','当前对话历史消息','count',jsonb_array_length(rows),'messages',rows);
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_knowledge(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_query TEXT,p_limit INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_query IS NULL OR length(btrim(p_query))=0 OR length(p_query)>200 OR p_limit IS NULL OR p_limit<1 OR p_limit>10 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_QUERY_INVALID'; END IF;
 SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('id',n.id,'category',n.category,'node_type',n.node_type,
    'title',left(n.title,200),'content',left(n.content,1000),'confidence',n.confidence,
    'source',n.source,'metadata',n.metadata) x
     FROM knowledge_nodes n
    WHERE n.is_deleted=FALSE
      AND ((c->>'org_id') IS NOT NULL AND n.org_id=(c->>'org_id')::UUID
        OR (c->>'org_id') IS NULL AND n.org_id IS NULL AND n.owner_user_id=(c->>'user_id')::UUID)
      AND (n.title ILIKE '%'||btrim(p_query)||'%' OR n.content ILIKE '%'||btrim(p_query)||'%')
    ORDER BY n.id LIMIT p_limit) q;
 RETURN jsonb_build_object('summary','知识库检索结果','count',jsonb_array_length(rows),'items',rows);
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_evidence(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_operation TEXT,p_artifact_id TEXT,p_selector TEXT,p_query TEXT,p_limit INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB; one JSONB;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_operation NOT IN ('search','get') THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_OPERATION_INVALID'; END IF;
 IF p_operation='search' THEN
  IF p_limit IS NULL OR p_limit<1 OR p_limit>10 OR length(COALESCE(p_query,''))>200 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_LIMIT_INVALID'; END IF;
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('artifact_id',e.artifact_id,'source',e.source,'columns',e.columns,
     'query_scope',e.query_scope,'byte_size',e.byte_size,'context_revision',e.context_revision) x
    FROM conversation_data_evidence e
   WHERE e.conversation_id=(c->>'conversation_id')::UUID
     AND e.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID
     AND e.validation_status='ready' AND e.context_revision<=(c->>'context_revision')::BIGINT
     AND (COALESCE(p_query,'')='' OR e.source ILIKE '%'||p_query||'%')
   ORDER BY e.context_revision DESC LIMIT p_limit) q;
  RETURN jsonb_build_object('summary','Evidence 检索结果','count',jsonb_array_length(rows),'evidence',rows);
 END IF;
 IF p_artifact_id IS NULL OR length(p_artifact_id)>160 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_REFERENCE_INVALID'; END IF;
 SELECT jsonb_build_object('artifact_id',e.artifact_id,'source',e.source,'columns',e.columns,
   'rows',CASE WHEN p_selector='rows' THEN e.rows ELSE '[]'::JSONB END,
   'query_scope',e.query_scope,'metric_definitions',e.metric_definitions,
   'model_view',e.model_view,'byte_size',e.byte_size,'context_revision',e.context_revision)
   INTO one FROM conversation_data_evidence e
  WHERE e.conversation_id=(c->>'conversation_id')::UUID AND e.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID
    AND e.artifact_id=p_artifact_id AND e.validation_status='ready'
    AND e.context_revision<=(c->>'context_revision')::BIGINT;
 RETURN jsonb_build_object('summary','Evidence 详情','count',CASE WHEN one IS NULL THEN 0 ELSE 1 END,'evidence',COALESCE(one,'{}'::JSONB));
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_memory(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_operation TEXT,p_memory_id UUID,p_query TEXT,p_limit INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB; one JSONB;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_operation='search' THEN
  IF p_query IS NULL OR length(btrim(p_query))=0 OR p_limit IS NULL OR p_limit<1 OR p_limit>6 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_QUERY_INVALID'; END IF;
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('memory_ref','memory:'||m.id,'content',left(m.content,1000),
     'kind',COALESCE(m.metadata->>'kind','memory'),'valid_from',m.valid_from,'valid_until',m.valid_until,
     'source_message_ids',m.source_message_ids) x FROM memory_atoms m
    WHERE m.user_id=(c->>'user_id')::UUID AND m.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID
      AND m.is_deleted=FALSE AND m.status='active' AND m.content ILIKE '%'||btrim(p_query)||'%'
    ORDER BY m.id LIMIT p_limit) q;
  RETURN jsonb_build_object('summary','记忆检索结果','count',jsonb_array_length(rows),'memories',rows);
 END IF;
 IF p_operation<>'get' OR p_memory_id IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_REFERENCE_INVALID'; END IF;
 SELECT jsonb_build_object('memory_ref','memory:'||m.id,'content',left(m.content,1000),
   'kind',COALESCE(m.metadata->>'kind','memory'),'valid_from',m.valid_from,'valid_until',m.valid_until,
   'source_message_ids',m.source_message_ids) INTO one FROM memory_atoms m
  WHERE m.id=p_memory_id AND m.user_id=(c->>'user_id')::UUID
    AND m.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID AND m.is_deleted=FALSE AND m.status='active';
 RETURN jsonb_build_object('summary','记忆详情','count',CASE WHEN one IS NULL THEN 0 ELSE 1 END,'memory',COALESCE(one,'{}'::JSONB));
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_artifact(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_operation TEXT,p_artifact_id TEXT,p_query TEXT,p_limit INTEGER,p_cursor INTEGER,p_max_tokens INTEGER
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB; one JSONB; v_artifact conversation_artifacts%ROWTYPE; content TEXT;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_operation='search' THEN
  IF p_limit IS NULL OR p_limit<1 OR p_limit>20 OR length(COALESCE(p_query,''))>200 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_LIMIT_INVALID'; END IF;
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('artifact_ref','artifact:'||art.id,'artifact_type',art.artifact_type,'status',art.status,
    'byte_size',art.byte_size,'content_hash',art.content_hash,'model_view',COALESCE(art.history_view,art.model_view,'{}'::JSONB),'metadata',art.metadata,'context_revision',art.context_revision) x
    FROM conversation_artifacts art WHERE art.conversation_id=(c->>'conversation_id')::UUID
     AND art.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID AND art.status='ready'
     AND art.context_revision<=(c->>'context_revision')::BIGINT
     AND (COALESCE(p_query,'')='' OR art.tool_name ILIKE '%'||p_query||'%')
    ORDER BY art.context_revision DESC LIMIT p_limit) q;
  RETURN jsonb_build_object('summary','Artifact 检索结果','count',jsonb_array_length(rows),'artifacts',rows);
 END IF;
 IF p_artifact_id IS NULL OR length(p_artifact_id)>160 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_REFERENCE_INVALID'; END IF;
 SELECT * INTO v_artifact FROM conversation_artifacts x WHERE x.id=p_artifact_id::UUID
  AND x.conversation_id=(c->>'conversation_id')::UUID AND x.org_id IS NOT DISTINCT FROM (c->>'org_id')::UUID
  AND x.status='ready' AND x.context_revision<=(c->>'context_revision')::BIGINT;
 IF NOT FOUND THEN RETURN jsonb_build_object('summary','Artifact 不存在','count',0); END IF;
 IF p_operation='get' THEN RETURN jsonb_build_object('summary','Artifact 详情','artifact_ref','artifact:'||v_artifact.id,'artifact_type',v_artifact.artifact_type,'status',v_artifact.status,'byte_size',v_artifact.byte_size,'content_hash',v_artifact.content_hash,'model_view',COALESCE(v_artifact.history_view,v_artifact.model_view,'{}'::JSONB),'metadata',v_artifact.metadata,'context_revision',v_artifact.context_revision); END IF;
 IF p_operation<>'read' OR v_artifact.storage_kind='oss' OR p_cursor IS NULL OR p_cursor<0 OR p_cursor>16000 OR p_max_tokens IS NULL OR p_max_tokens<256 OR p_max_tokens>16000 THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_ARTIFACT_READ_INVALID'; END IF;
 IF v_artifact.storage_kind='inline' THEN content:=left(v_artifact.inline_content::TEXT,40000);
 ELSIF v_artifact.storage_kind='message_slice' THEN SELECT left(m.content,40000) INTO content FROM messages m WHERE m.id=(v_artifact.storage_ref->>'message_id')::UUID AND m.conversation_id=v_artifact.conversation_id AND m.context_revision<=(c->>'context_revision')::BIGINT;
 ELSE RAISE EXCEPTION 'AGENT_RUNTIME_READ_STORAGE_FORBIDDEN'; END IF;
 RETURN jsonb_build_object('summary','Artifact 分页内容','artifact_id',v_artifact.id,'content',COALESCE(substring(content FROM p_cursor+1 FOR p_max_tokens*3),'') ,'cursor',p_cursor,'next_cursor',NULL,'byte_size',octet_length(COALESCE(content,'')),'returned_bytes',octet_length(COALESCE(substring(content FROM p_cursor+1 FOR p_max_tokens*3),'')),'complete',TRUE);
END $$;

CREATE OR REPLACE FUNCTION read_agent_runtime_erp(
    p_action_id UUID,p_attempt_id UUID,p_execution_token UUID,p_request_hash TEXT,
    p_executor_type TEXT,p_executor_revision INTEGER,
    p_operation TEXT,p_code TEXT DEFAULT NULL,p_name TEXT DEFAULT NULL,p_spec TEXT DEFAULT NULL,
    p_product_code TEXT DEFAULT NULL,p_start_date TEXT DEFAULT NULL,p_end_date TEXT DEFAULT NULL,
    p_num_iid TEXT DEFAULT NULL,p_doc_type TEXT DEFAULT NULL,p_compare_kind TEXT DEFAULT NULL,
    p_current_period TEXT DEFAULT NULL,p_shop_name TEXT DEFAULT NULL,p_platform TEXT DEFAULT NULL,
    p_supplier_name TEXT DEFAULT NULL,p_warehouse_name TEXT DEFAULT NULL,p_is_virtual BOOLEAN DEFAULT NULL,
    p_category TEXT DEFAULT NULL,p_status INTEGER DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c JSONB; rows JSONB; current_value JSONB; baseline_value JSONB;
        v_end TIMESTAMPTZ:=clock_timestamp(); v_start TIMESTAMPTZ;
        v_baseline_start TIMESTAMPTZ; v_baseline_end TIMESTAMPTZ;
        v_delta INTERVAL;
BEGIN
 c:=_agent_runtime_read_context(p_action_id,p_attempt_id,p_execution_token,p_request_hash,p_executor_type,p_executor_revision);
 IF p_operation='product' THEN
  IF COALESCE(p_code,p_name,p_spec) IS NULL THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_IDENTIFIER_REQUIRED'; END IF;
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (
   SELECT jsonb_build_object('outer_id',p.outer_id,'sku_outer_id',NULL,'title',p.title,'properties_name',NULL,'shipper',p.shipper,'active_status',p.active_status,'barcode',p.barcode) x
    FROM erp_products p WHERE p.org_id=(c->>'org_id')::UUID AND ((p_code IS NOT NULL AND (p.outer_id=p_code OR p.barcode=p_code)) OR (p_name IS NOT NULL AND p.title ILIKE '%'||p_name||'%'))
   UNION ALL
   SELECT jsonb_build_object('outer_id',s.outer_id,'sku_outer_id',s.sku_outer_id,'title',NULL,'properties_name',s.properties_name,'shipper',NULL,'active_status',NULL,'barcode',s.barcode) x
    FROM erp_product_skus s WHERE s.org_id=(c->>'org_id')::UUID AND ((p_code IS NOT NULL AND (s.sku_outer_id=p_code OR s.barcode=p_code)) OR (p_spec IS NOT NULL AND s.properties_name ILIKE '%'||p_spec||'%')) LIMIT 20) q;
  RETURN jsonb_build_object('summary','商品编码识别结果','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='stock' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('outer_id',s.outer_id,'sku_outer_id',s.sku_outer_id,'warehouse_id',s.warehouse_id,'sellable_num',s.sellable_num,'total_stock',s.total_stock,'lock_stock',s.lock_stock,'purchase_num',s.purchase_num,'stock_status',s.stock_status) x FROM erp_stock_status s WHERE s.org_id=(c->>'org_id')::UUID AND (s.outer_id=p_product_code OR s.sku_outer_id=p_product_code) LIMIT 100);
  RETURN jsonb_build_object('summary','库存查询结果','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='stats' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('stat_date',s.stat_date,'order_count',s.order_count,'order_qty',s.order_qty,'order_amount',s.order_amount,'purchase_count',s.purchase_count,'purchase_qty',s.purchase_qty,'receipt_count',s.receipt_count,'receipt_qty',s.receipt_qty,'aftersale_count',s.aftersale_count,'aftersale_qty',s.aftersale_qty) x FROM erp_product_daily_stats s WHERE s.org_id=(c->>'org_id')::UUID AND s.outer_id=p_product_code AND (p_start_date IS NULL OR s.stat_date>=p_start_date::DATE) AND (p_end_date IS NULL OR s.stat_date<=p_end_date::DATE) ORDER BY s.stat_date DESC LIMIT 100);
  RETURN jsonb_build_object('summary','商品统计结果','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='platform' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('outer_id',m.outer_id,'num_iid',m.num_iid,'user_id',m.user_id,'sku_mappings',m.sku_mappings) x FROM erp_product_platform_map m WHERE m.org_id=(c->>'org_id')::UUID AND (p_code IS NULL OR m.outer_id=p_code) AND (p_num_iid IS NULL OR m.num_iid=p_num_iid) LIMIT 100);
  RETURN jsonb_build_object('summary','平台映射结果','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='shops' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('name',s.name,'platform',s.platform,'state',s.state,'shop_id',s.shop_id,'short_name',s.short_name) x FROM erp_shops s WHERE s.org_id=(c->>'org_id')::UUID AND (p_platform IS NULL OR s.platform=p_platform) ORDER BY s.platform LIMIT 100);
  RETURN jsonb_build_object('summary','店铺列表','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='warehouses' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('warehouse_id',w.warehouse_id,'name',w.name,'code',w.code,'warehouse_type',w.warehouse_type,'status',w.status,'is_virtual',w.is_virtual) x FROM erp_warehouses w WHERE w.org_id=(c->>'org_id')::UUID AND (p_is_virtual IS NULL OR w.is_virtual=p_is_virtual) ORDER BY w.name LIMIT 100);
  RETURN jsonb_build_object('summary','仓库列表','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='suppliers' THEN
  SELECT COALESCE(jsonb_agg(x),'[]'::JSONB) INTO rows FROM (SELECT jsonb_build_object('code',s.code,'name',s.name,'status',s.status,'contact_name',s.contact_name,'category_name',s.category_name,'remark',s.remark) x FROM erp_suppliers s WHERE s.org_id=(c->>'org_id')::UUID AND (p_category IS NULL OR s.category_name ILIKE '%'||p_category||'%') AND (p_status IS NULL OR s.status=p_status) ORDER BY s.name LIMIT 100);
  RETURN jsonb_build_object('summary','供应商列表','count',jsonb_array_length(rows),'items',rows);
 ELSIF p_operation='compare' THEN
  IF p_doc_type IS NULL OR p_compare_kind NOT IN ('wow','yoy') OR p_current_period NOT IN ('today','yesterday','this_week','this_month') THEN RAISE EXCEPTION 'AGENT_RUNTIME_READ_COMPARE_INVALID'; END IF;
  IF p_current_period='today' THEN v_start:=date_trunc('day',v_end);
  ELSIF p_current_period='yesterday' THEN v_end:=date_trunc('day',v_end); v_start:=v_end-interval '1 day';
  ELSIF p_current_period='this_week' THEN v_start:=date_trunc('week',v_end);
  ELSE v_start:=date_trunc('month',v_end); END IF;
  v_delta:=CASE WHEN p_compare_kind='wow' THEN interval '7 days' ELSE interval '365 days' END;
  v_baseline_start:=v_start-v_delta; v_baseline_end:=v_end-v_delta;
  SELECT jsonb_build_object('doc_count',count(DISTINCT d.doc_id),'total_qty',COALESCE(sum(d.quantity),0),'total_amount',COALESCE(sum(d.amount),0)) INTO current_value FROM erp_document_items d WHERE d.org_id=(c->>'org_id')::UUID AND d.doc_type=p_doc_type AND d.doc_created_at>=v_start AND d.doc_created_at<v_end;
  SELECT jsonb_build_object('doc_count',count(DISTINCT d.doc_id),'total_qty',COALESCE(sum(d.quantity),0),'total_amount',COALESCE(sum(d.amount),0)) INTO baseline_value FROM erp_document_items d WHERE d.org_id=(c->>'org_id')::UUID AND d.doc_type=p_doc_type AND d.doc_created_at>=v_baseline_start AND d.doc_created_at<v_baseline_end;
  RETURN jsonb_build_object('summary','对比统计结果','current',current_value,'baseline',baseline_value,'compare_kind',p_compare_kind);
 END IF;
 RAISE EXCEPTION 'AGENT_RUNTIME_READ_OPERATION_INVALID';
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_read_context(UUID,UUID,UUID,TEXT,TEXT,INTEGER),
 read_agent_runtime_conversation(UUID,UUID,UUID,TEXT,TEXT,INTEGER,INTEGER),
 read_agent_runtime_knowledge(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,INTEGER),
 read_agent_runtime_evidence(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,INTEGER),
 read_agent_runtime_memory(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,UUID,TEXT,INTEGER),
 read_agent_runtime_artifact(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,INTEGER,INTEGER,INTEGER),
read_agent_runtime_erp(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BOOLEAN,TEXT,INTEGER)
 FROM PUBLIC,everydayai,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,
 everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION
 read_agent_runtime_conversation(UUID,UUID,UUID,TEXT,TEXT,INTEGER,INTEGER),
 read_agent_runtime_knowledge(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,INTEGER),
 read_agent_runtime_evidence(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,INTEGER),
 read_agent_runtime_memory(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,UUID,TEXT,INTEGER),
 read_agent_runtime_artifact(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,INTEGER,INTEGER,INTEGER),
 read_agent_runtime_erp(UUID,UUID,UUID,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BOOLEAN,TEXT,INTEGER)
 TO everydayai_agent_runtime_worker;
REVOKE ALL ON TABLE messages,knowledge_nodes,conversation_data_evidence,memory_atoms,conversation_artifacts,
 erp_products,erp_product_skus,erp_stock_status,erp_product_daily_stats,erp_product_platform_map,
 erp_shops,erp_warehouses,erp_suppliers,erp_document_items
 FROM everydayai_agent_runtime_worker;
RESET ROLE;
