-- Disable direct requests before reverting this transition. Existing committing rows remain resumable.

CREATE OR REPLACE FUNCTION public.transition_change_set(
    p_change_set_id UUID,
    p_org_id UUID,
    p_expected_status TEXT,
    p_next_status TEXT,
    p_actor_id TEXT,
    p_actor_type TEXT,
    p_event_type TEXT,
    p_payload JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_row public.change_sets%ROWTYPE;
    v_from_status TEXT;
    v_sequence BIGINT;
BEGIN
    SELECT * INTO v_row
      FROM public.change_sets
     WHERE id = p_change_set_id AND org_id = p_org_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;

    IF v_row.status <> p_expected_status THEN
        RETURN jsonb_build_object('outcome', 'state_conflict', 'change_set', to_jsonb(v_row));
    END IF;

    -- 到期优先于后续人工操作；committing 由恢复器处理，避免业务提交中途被过期抢占。
    IF v_row.expires_at <= NOW()
       AND v_row.status NOT IN ('applied', 'cancelled', 'rejected', 'failed', 'expired', 'conflicted', 'committing')
       AND p_next_status <> 'expired' THEN
        v_from_status := v_row.status;
        UPDATE public.change_sets
           SET status = 'expired', updated_by = p_actor_id, updated_by_type = p_actor_type,
               revision = revision + 1, updated_at = NOW(),
               audit_subject = audit_subject || jsonb_build_object('last_actor_id', p_actor_id, 'last_actor_type', p_actor_type)
         WHERE id = v_row.id
         RETURNING * INTO v_row;
        SELECT COALESCE(MAX(sequence), 0) + 1 INTO v_sequence
          FROM public.change_events WHERE change_set_id = v_row.id;
        INSERT INTO public.change_events(
            change_set_id, org_id, sequence, event_type, from_status, to_status,
            actor_id, actor_type, payload
        ) VALUES (
            v_row.id, v_row.org_id, v_sequence, 'expired', v_from_status, 'expired',
            p_actor_id, p_actor_type, '{}'::JSONB
        );
        RETURN jsonb_build_object('outcome', 'expired', 'change_set', to_jsonb(v_row));
    END IF;

    IF NOT (
        (v_row.status = 'draft' AND p_next_status IN ('resolving', 'cancelled', 'expired')) OR
        (v_row.status = 'resolving' AND p_next_status IN ('proposed', 'failed', 'cancelled', 'expired')) OR
        (v_row.status = 'proposed' AND p_next_status IN ('validating', 'rejected', 'failed', 'cancelled', 'expired')) OR
        (v_row.status = 'validating' AND p_next_status IN ('preflighting', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'preflighting' AND p_next_status IN ('awaiting_approval', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'awaiting_approval' AND p_next_status IN ('committing', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'committing' AND p_next_status IN ('applied', 'failed', 'conflicted'))
    ) THEN
        RETURN jsonb_build_object('outcome', 'invalid_transition', 'change_set', to_jsonb(v_row));
    END IF;

    v_from_status := v_row.status;
    UPDATE public.change_sets
       SET status = p_next_status,
           updated_by = p_actor_id,
           updated_by_type = p_actor_type,
           committed_revision = CASE WHEN p_next_status = 'applied' THEN p_payload->>'new_revision' ELSE committed_revision END,
           error_code = CASE WHEN p_next_status = 'failed' THEN COALESCE(p_payload->>'error_type', 'changeset_failed') ELSE error_code END,
           error_message = CASE WHEN p_next_status = 'failed' THEN p_payload->>'error_message' ELSE error_message END,
           conflict = CASE WHEN p_next_status = 'conflicted' THEN p_payload ELSE conflict END,
           audit_subject = audit_subject || jsonb_build_object('last_actor_id', p_actor_id, 'last_actor_type', p_actor_type),
           revision = revision + 1,
           updated_at = NOW()
     WHERE id = v_row.id
     RETURNING * INTO v_row;
    SELECT COALESCE(MAX(sequence), 0) + 1 INTO v_sequence
      FROM public.change_events WHERE change_set_id = v_row.id;
    INSERT INTO public.change_events(
        change_set_id, org_id, sequence, event_type, from_status, to_status,
        actor_id, actor_type, payload
    ) VALUES (
        v_row.id, v_row.org_id, v_sequence, p_event_type, v_from_status, p_next_status,
        p_actor_id, p_actor_type, COALESCE(p_payload, '{}'::JSONB)
    );
    RETURN jsonb_build_object('outcome', 'transitioned', 'change_set', to_jsonb(v_row));
END;
$$;
