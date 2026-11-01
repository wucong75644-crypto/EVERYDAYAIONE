RESET ROLE;
DO $restore$ DECLARE installed record; BEGIN
    SELECT prosrc,pg_get_userbyid(proowner) owner_name INTO installed FROM pg_proc
      WHERE oid='public.pause_generation_turn_owned(uuid,uuid,text)'::regprocedure;
    IF installed.owner_name NOT IN ('everydayai','everydayai_owner') OR md5(installed.prosrc)<>'6d1d2d21ed74a8f04a9f676b99d9851f' THEN
        RAISE EXCEPTION 'TASK_ACTOR_CONTROL_RESTORE_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',installed.owner_name);
    DROP FUNCTION public.pause_generation_turn_owned(uuid,uuid,text);
    ALTER FUNCTION public.agent_task_legacy_pause_generation_turn_owned(uuid,uuid,text) RENAME TO pause_generation_turn_owned;
END $restore$;
RESET ROLE;
DO $restore$ DECLARE installed record; BEGIN
    SELECT prosrc,pg_get_userbyid(proowner) owner_name INTO installed FROM pg_proc
      WHERE oid='public.cancel_generation_turn_owned(uuid,uuid,text)'::regprocedure;
    IF installed.owner_name NOT IN ('everydayai','everydayai_owner') OR md5(installed.prosrc)<>'3c90ec83dbce2b8b433a61e11be53d92' THEN
        RAISE EXCEPTION 'TASK_ACTOR_CONTROL_RESTORE_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',installed.owner_name);
    DROP FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text);
    ALTER FUNCTION public.agent_task_legacy_cancel_generation_turn_owned(uuid,uuid,text) RENAME TO cancel_generation_turn_owned;
END $restore$;
RESET ROLE;
