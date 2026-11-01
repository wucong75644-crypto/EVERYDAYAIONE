-- Lock conversation before task in Actor control terminal paths.
-- Preserve each installed owner, invocation body and execute ACL.
RESET ROLE;
DO $install$ DECLARE installed record; permission record; BEGIN
    SELECT proowner,prosrc,proacl,prosecdef,pg_get_userbyid(proowner) owner_name INTO installed FROM pg_proc
      WHERE oid='public.pause_generation_turn_owned(uuid,uuid,text)'::regprocedure;
    IF installed.owner_name NOT IN ('everydayai','everydayai_owner') OR installed.prosecdef
       -- Exact reviewed repository and installed production variants. Keep the
       -- original body (including its snapshot helper choice) behind the wrapper.
       OR md5(installed.prosrc) NOT IN ('4b315fa32edae89ca625d343cd51b20e','c21ad0c393cf26acaa85375f757cfd19')
       OR to_regprocedure('public.agent_task_legacy_pause_generation_turn_owned(uuid,uuid,text)') IS NOT NULL THEN
        RAISE EXCEPTION 'TASK_ACTOR_CONTROL_CONTRACT_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',installed.owner_name);
    ALTER FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) RENAME TO agent_task_legacy_pause_generation_turn_owned;
    EXECUTE $definition$CREATE FUNCTION public.pause_generation_turn_owned(p_task_id uuid,p_execution_token uuid,p_reason text DEFAULT 'user_paused')
      RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
BEGIN
    PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
    RETURN public.agent_task_legacy_pause_generation_turn_owned(p_task_id,p_execution_token,p_reason);
END;
$body$;$definition$;
    -- Clear creation defaults, then copy the exact original execute rights.
    FOR permission IN SELECT grantee FROM aclexplode((SELECT COALESCE(proacl,acldefault('f',proowner)) FROM pg_proc
        WHERE oid='public.pause_generation_turn_owned(uuid,uuid,text)'::regprocedure)) LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) FROM %s',
            CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(permission.grantee)) END);
    END LOOP;
    FOR permission IN SELECT * FROM aclexplode(COALESCE(installed.proacl,acldefault('f',installed.proowner))) LOOP
        IF permission.privilege_type='EXECUTE' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) TO %s%s',
                CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(permission.grantee)) END,
                CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
        END IF;
    END LOOP;
END $install$;
RESET ROLE;
DO $install$ DECLARE installed record; permission record; BEGIN
    SELECT proowner,prosrc,proacl,prosecdef,pg_get_userbyid(proowner) owner_name INTO installed FROM pg_proc
      WHERE oid='public.cancel_generation_turn_owned(uuid,uuid,text)'::regprocedure;
    IF installed.owner_name NOT IN ('everydayai','everydayai_owner') OR installed.prosecdef
       OR md5(installed.prosrc) NOT IN ('83bee41969f36ac93b23dad23f5c582b','bd171df8d471c36ab93aa1a3d7383997')
       OR to_regprocedure('public.agent_task_legacy_cancel_generation_turn_owned(uuid,uuid,text)') IS NOT NULL THEN
        RAISE EXCEPTION 'TASK_ACTOR_CONTROL_CONTRACT_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',installed.owner_name);
    ALTER FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) RENAME TO agent_task_legacy_cancel_generation_turn_owned;
    EXECUTE $definition$CREATE FUNCTION public.cancel_generation_turn_owned(p_task_id uuid,p_execution_token uuid,p_reason text DEFAULT 'user_cancelled')
      RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
BEGIN
    PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
    RETURN public.agent_task_legacy_cancel_generation_turn_owned(p_task_id,p_execution_token,p_reason);
END;
$body$;$definition$;
    -- Clear creation defaults, then copy the exact original execute rights.
    FOR permission IN SELECT grantee FROM aclexplode((SELECT COALESCE(proacl,acldefault('f',proowner)) FROM pg_proc
        WHERE oid='public.cancel_generation_turn_owned(uuid,uuid,text)'::regprocedure)) LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) FROM %s',
            CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(permission.grantee)) END);
    END LOOP;
    FOR permission IN SELECT * FROM aclexplode(COALESCE(installed.proacl,acldefault('f',installed.proowner))) LOOP
        IF permission.privilege_type='EXECUTE' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) TO %s%s',
                CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(permission.grantee)) END,
                CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
        END IF;
    END LOOP;
END $install$;
RESET ROLE;
