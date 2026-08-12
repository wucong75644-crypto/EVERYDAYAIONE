-- 227.57: Attempt-fenced facade over the existing ERP analytics RPCs.
-- The Runtime Worker receives no ERP table privileges and cannot supply org_id.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION execute_agent_runtime_local_query_v1(
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT,
 p_rpc_name TEXT,p_action_arguments JSONB,p_params JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
 ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 x agent_actions%ROWTYPE; a agent_action_attempts%ROWTYPE;
 result JSONB; metrics TEXT[]; buckets NUMERIC[];
 rpc_name TEXT:=btrim(COALESCE(p_rpc_name,''));
 doc_type TEXT; metric TEXT; query_type TEXT;
 start_text TEXT; end_text TEXT; request_metrics JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_attempt_id IS NULL OR NULLIF(btrim(p_worker_id),'') IS NULL
 OR p_execution_token IS NULL OR p_expected_attempt_version IS NULL
 OR p_expected_attempt_version<0
 OR COALESCE(p_request_hash,'')!~'^[0-9a-f]{64}$'
 OR jsonb_typeof(p_action_arguments) IS DISTINCT FROM 'object'
 OR jsonb_typeof(p_params) IS DISTINCT FROM 'object'
 OR pg_column_size(p_params)>32768 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=a.session_id FOR SHARE;
 SELECT * INTO r FROM agent_runs WHERE id=a.run_id FOR SHARE;
 SELECT * INTO x FROM agent_actions WHERE id=a.action_id FOR SHARE;
 SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
 IF ss.id IS NULL OR r.id IS NULL OR x.id IS NULL
 OR r.session_id IS DISTINCT FROM ss.id OR x.session_id IS DISTINCT FROM ss.id
 OR a.session_id IS DISTINCT FROM ss.id OR x.run_id IS DISTINCT FROM r.id
 OR a.run_id IS DISTINCT FROM r.id OR a.action_id IS DISTINCT FROM x.id
 OR r.org_id IS DISTINCT FROM ss.org_id OR x.org_id IS DISTINCT FROM ss.org_id
 OR a.org_id IS DISTINCT FROM ss.org_id
 OR r.user_id IS DISTINCT FROM ss.user_id OR x.user_id IS DISTINCT FROM ss.user_id
 OR a.user_id IS DISTINCT FROM ss.user_id OR x.tool_name<>'local_data'
 OR x.arguments IS DISTINCT FROM p_action_arguments
 OR x.policy_decision NOT IN ('preauthorized','requires_authorization')
 OR r.status<>'running' OR a.status<>'dispatching'
 OR a.dispatch_phase<>'request_started'
 OR a.worker_id IS DISTINCT FROM btrim(p_worker_id)
 OR a.execution_token IS DISTINCT FROM p_execution_token
 OR a.request_hash IS DISTINCT FROM p_request_hash
 OR a.state_version<>p_expected_attempt_version
 OR r.lease_expires_at<=clock_timestamp()
 OR a.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(
  SELECT 1 FROM agent_action_dispatch_intents intent
  JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
   WHERE intent.attempt_id=a.id AND intent.action_id=x.id
     AND intent.execution_token=p_execution_token
     AND intent.request_hash=p_request_hash
     AND intent.executor_type='runtime_artifact_job:local_data'
     AND intent.executor_revision=1
     AND intent.recovery_mode='idempotent_replay'
     AND receipt.action_id=x.id AND receipt.decision='allow'
     AND receipt.session_id=ss.id AND receipt.run_id=r.id
     AND receipt.org_id=ss.org_id
     AND receipt.user_id IS NOT DISTINCT FROM ss.user_id
     AND receipt.arguments_hash=x.arguments_hash
     AND receipt.executor_type=intent.executor_type
     AND receipt.executor_revision=intent.executor_revision
     AND intent.policy_revision=receipt.policy_revision
     AND receipt.policy_revision=x.policy_revision
     AND receipt.expires_at>clock_timestamp()
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 PERFORM _agent_runtime_assert_facts_epoch(
  a.id,p_execution_token,a.org_id,NULL::TEXT,NULL::TEXT,'new'::TEXT
 );

 query_type:=p_action_arguments->>'query_type';
 doc_type:=p_action_arguments->>'doc_type';
 request_metrics:=p_action_arguments->'metrics';
 IF query_type NOT IN ('trend','compare','cross','distribution')
 OR doc_type NOT IN (
  'order','purchase','aftersale','receipt','shelf','purchase_return',
  'stock','daily_stats'
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_MODE_DISABLED' USING ERRCODE='42501';
 END IF;
 IF p_params ? 'p_org_id' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_ORG_OVERRIDE' USING ERRCODE='42501';
 END IF;
 start_text:=p_params->>'p_start'; end_text:=p_params->>'p_end';
 IF NULLIF(start_text,'') IS NULL OR NULLIF(end_text,'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_TIME_RANGE_REQUIRED' USING ERRCODE='22023';
 END IF;

 IF rpc_name='erp_trend_query' AND query_type='trend' THEN
  IF jsonb_typeof(COALESCE(p_params->'p_metrics','[]'::JSONB))<>'array' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_METRICS_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(array_agg(value),'{}'::TEXT[]) INTO metrics
   FROM jsonb_array_elements_text(COALESCE(p_params->'p_metrics','[]'::JSONB)) value;
  SELECT erp_trend_query(
   ss.org_id,start_text::DATE,end_text::DATE,
   COALESCE(NULLIF(p_params->>'p_granularity',''),'day'),metrics,
   NULLIF(p_params->>'p_group_by',''),NULLIF(p_params->>'p_outer_id',''),
   NULLIF(p_params->>'p_platform',''),NULLIF(p_params->>'p_shop_name',''),
   LEAST(GREATEST(COALESCE((p_params->>'p_limit')::INT,366),1),366)
  ) INTO result;
 ELSIF rpc_name='erp_global_stats_query' AND query_type='compare' THEN
  IF doc_type NOT IN ('order','purchase','aftersale','receipt','shelf','purchase_return')
  OR p_params->'p_filters' IS NOT NULL AND p_params->'p_filters'<>'null'::JSONB THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_COMPARE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT erp_global_stats_query(
   doc_type,start_text::TIMESTAMPTZ,end_text::TIMESTAMPTZ,'doc_created_at',
   NULLIF(p_params->>'p_shop',''),NULLIF(p_params->>'p_platform',''),NULL,NULL,
   NULLIF(p_params->>'p_group_by',''),
   LEAST(GREATEST(COALESCE((p_params->>'p_limit')::INT,20),1),100),ss.org_id,NULL
  ) INTO result;
 ELSIF rpc_name='erp_cross_metric_query' AND query_type='cross' THEN
  metric:=p_params->>'p_metric';
  IF jsonb_typeof(request_metrics)<>'array'
  OR request_metrics->>0 IS DISTINCT FROM metric
  OR metric NOT IN (
   'return_rate','refund_rate','exchange_rate','aftersale_rate',
   'avg_order_value','gross_margin','gross_profit','purchase_fulfillment',
   'shelf_rate','supplier_return_rate'
  ) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_METRIC_DISABLED' USING ERRCODE='42501';
  END IF;
  SELECT erp_cross_metric_query(
   ss.org_id,start_text::DATE,end_text::DATE,metric,
   NULLIF(p_params->>'p_group_by',''),NULLIF(p_params->>'p_granularity',''),
   NULLIF(p_params->>'p_outer_id',''),NULLIF(p_params->>'p_platform',''),
   NULLIF(p_params->>'p_shop_name',''),
   LEAST(GREATEST(COALESCE((p_params->>'p_limit')::INT,50),1),500)
  ) INTO result;
 ELSIF rpc_name='erp_repurchase_rate_query' AND query_type='cross' THEN
  IF jsonb_typeof(request_metrics)<>'array'
  OR request_metrics->>0 IS DISTINCT FROM 'repurchase_rate' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_METRIC_DISABLED' USING ERRCODE='42501';
  END IF;
  SELECT erp_repurchase_rate_query(
   ss.org_id,start_text::TIMESTAMPTZ,end_text::TIMESTAMPTZ,
   NULLIF(p_params->>'p_group_by',''),NULLIF(p_params->>'p_platform',''),
   NULLIF(p_params->>'p_shop_name',''),
   LEAST(GREATEST(COALESCE((p_params->>'p_limit')::INT,50),1),200)
  ) INTO result;
 ELSIF rpc_name='erp_ship_time_query' AND query_type='cross' THEN
  metric:=request_metrics->>0;
  IF jsonb_typeof(request_metrics)<>'array'
  OR metric NOT IN ('avg_ship_time','same_day_rate') THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_METRIC_DISABLED' USING ERRCODE='42501';
  END IF;
  SELECT erp_ship_time_query(
   ss.org_id,start_text::TIMESTAMPTZ,end_text::TIMESTAMPTZ,
   NULLIF(p_params->>'p_group_by',''),NULLIF(p_params->>'p_platform',''),
   NULLIF(p_params->>'p_shop_name',''),
   LEAST(GREATEST(COALESCE((p_params->>'p_limit')::INT,50),1),200)
  ) INTO result;
 ELSIF rpc_name='erp_distribution_query' AND query_type='distribution' THEN
  IF jsonb_typeof(COALESCE(p_params->'p_buckets','[]'::JSONB))<>'array' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_BUCKETS_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(array_agg(value::NUMERIC),'{}'::NUMERIC[]) INTO buckets
   FROM jsonb_array_elements_text(COALESCE(p_params->'p_buckets','[]'::JSONB)) value;
  IF cardinality(buckets)<2 OR cardinality(buckets)>20 THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_BUCKETS_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT erp_distribution_query(
   ss.org_id,p_params->>'p_table',NULLIF(p_params->>'p_doc_type',''),
   start_text,end_text,p_params->>'p_field',buckets,p_params->>'p_time_col'
  ) INTO result;
 ELSE
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_RPC_DISABLED' USING ERRCODE='42501';
 END IF;
 RETURN COALESCE(result,'[]'::JSONB);
EXCEPTION
 WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_LOCAL_QUERY_PARAMETERS_INVALID' USING ERRCODE='22023';
END $$;

REVOKE ALL ON FUNCTION execute_agent_runtime_local_query_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB
) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION execute_agent_runtime_local_query_v1(
 UUID,TEXT,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB
) TO everydayai_agent_runtime_worker;

RESET ROLE;
