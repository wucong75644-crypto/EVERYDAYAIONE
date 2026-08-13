SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION read_agent_runtime_ecom_model_v1(UUID,UUID,UUID,TEXT)
 FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
 everydayai_worker, everydayai_sync, everydayai;
DROP FUNCTION read_agent_runtime_ecom_model_v1(UUID,UUID,UUID,TEXT);
RESET ROLE;
