-- 116: 企微用户重复账号根治
-- 根因：get_or_create_user 是「查后写」模式，并发时多个请求同时通过
-- _find_mapping 检查 → 创建 N 个 users + 漏写 mappings + N × 100 积分赠送。
-- 详情见 commit 6ef699c 之后的根因分析。
--
-- 三件套：
-- 1) UNIQUE 索引（NULLS NOT DISTINCT）：DB 层兜底，并发任意 N 个 INSERT 最多成功 1 个
-- 2) wecom_get_or_create_user RPC：advisory lock + 单事务 user/mapping/credits 原子化
-- 3) merge_wecom_duplicate_users RPC：把 drop 用户的所有 FK 引用迁到 keep

-- ============================================================
-- 1. 唯一约束（最终防线）
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS wecom_mappings_uniq_idx
  ON wecom_user_mappings (wecom_userid, corp_id, org_id)
  NULLS NOT DISTINCT;

COMMENT ON INDEX wecom_mappings_uniq_idx IS
  '同一 (wecom_userid, corp_id, org_id) 全局唯一，NULL org_id 也视为相同值';


-- ============================================================
-- 2. 原子查询或创建 RPC
-- ============================================================

CREATE OR REPLACE FUNCTION wecom_get_or_create_user(
  p_wecom_userid TEXT,
  p_corp_id TEXT,
  p_org_id UUID,
  p_channel TEXT,
  p_display_name TEXT
) RETURNS JSONB AS $$
DECLARE
  v_user_id UUID;
  v_existing UUID;
  v_lock_key BIGINT;
BEGIN
  -- advisory lock：同一 (wecom_userid, corp_id) 的并发请求串行化
  -- xact_lock = 事务结束自动释放，无泄漏风险
  v_lock_key := hashtextextended(p_wecom_userid || '::' || p_corp_id, 0);
  PERFORM pg_advisory_xact_lock(v_lock_key);

  -- 锁后 double-check：另一个并发请求可能已创建
  SELECT user_id INTO v_existing
  FROM wecom_user_mappings
  WHERE wecom_userid = p_wecom_userid
    AND corp_id = p_corp_id
    AND org_id IS NOT DISTINCT FROM p_org_id;

  IF v_existing IS NOT NULL THEN
    UPDATE users SET last_login_at = NOW() WHERE id = v_existing;
    RETURN jsonb_build_object(
      'user_id', v_existing,
      'is_new', false
    );
  END IF;

  -- 创建用户（单事务内：失败任一步全回滚）
  INSERT INTO users (
    nickname, login_methods, created_by, role, credits, status, last_login_at
  ) VALUES (
    p_display_name, '["wecom"]'::jsonb, 'wecom'::user_created_by,
    'user'::user_role, 100, 'active'::account_status, NOW()
  ) RETURNING id INTO v_user_id;

  -- 创建映射（依赖 wecom_mappings_uniq_idx 兜底）
  INSERT INTO wecom_user_mappings (
    wecom_userid, corp_id, user_id, channel, wecom_nickname, org_id
  ) VALUES (
    p_wecom_userid, p_corp_id, v_user_id, p_channel, p_display_name, p_org_id
  );

  -- 注册赠送积分
  INSERT INTO credits_history (
    user_id, change_amount, balance_after, change_type, description, org_id
  ) VALUES (
    v_user_id, 100, 100, 'register_gift'::credits_change_type,
    '企业微信用户注册赠送积分', p_org_id
  );

  RETURN jsonb_build_object(
    'user_id', v_user_id,
    'is_new', true
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION wecom_get_or_create_user IS
  '企微用户原子查询或创建：advisory lock + 单事务 + 唯一约束三重防并发';


-- ============================================================
-- 3. 合并重复用户 RPC
-- ============================================================

CREATE OR REPLACE FUNCTION merge_wecom_duplicate_users(
  p_keep_uid UUID,
  p_drop_uids UUID[]
) RETURNS JSONB AS $$
DECLARE
  v_moved_credits INT := 0;
  v_moved_convs INT := 0;
  v_moved_imgs INT := 0;
  v_moved_tasks INT := 0;
  v_moved_credits_hist INT := 0;
  v_moved_mappings INT := 0;
BEGIN
  -- 安全校验
  IF p_keep_uid = ANY(p_drop_uids) THEN
    RAISE EXCEPTION 'keep_uid 不能在 drop_uids 中';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM users WHERE id = p_keep_uid) THEN
    RAISE EXCEPTION 'keep_uid 不存在: %', p_keep_uid;
  END IF;

  -- 累加 drop 的积分到 keep（先查后改，避免漏积分）
  SELECT COALESCE(SUM(credits), 0) INTO v_moved_credits
  FROM users WHERE id = ANY(p_drop_uids);

  UPDATE users SET credits = credits + v_moved_credits, updated_at = NOW()
  WHERE id = p_keep_uid;

  -- 迁移所有 FK 引用到 keep_uid
  UPDATE conversations SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  GET DIAGNOSTICS v_moved_convs = ROW_COUNT;

  UPDATE image_generations SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  GET DIAGNOSTICS v_moved_imgs = ROW_COUNT;

  UPDATE tasks SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  GET DIAGNOSTICS v_moved_tasks = ROW_COUNT;

  UPDATE credits_history SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  GET DIAGNOSTICS v_moved_credits_hist = ROW_COUNT;

  -- mapping 迁移：用 UPDATE ... ON CONFLICT DO NOTHING 兼容 keep 已有 mapping 的场景
  -- （UPDATE 不支持 ON CONFLICT，需用 DELETE 冲突项策略）
  -- 先删 drop 中与 keep 已有 mapping 重复的项
  DELETE FROM wecom_user_mappings
  WHERE user_id = ANY(p_drop_uids)
    AND EXISTS (
      SELECT 1 FROM wecom_user_mappings keep_m
      WHERE keep_m.user_id = p_keep_uid
        AND keep_m.wecom_userid = wecom_user_mappings.wecom_userid
        AND keep_m.corp_id = wecom_user_mappings.corp_id
        AND keep_m.org_id IS NOT DISTINCT FROM wecom_user_mappings.org_id
    );
  -- 剩下的迁移到 keep
  UPDATE wecom_user_mappings SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  GET DIAGNOSTICS v_moved_mappings = ROW_COUNT;

  -- org_members 类似：去重后迁移
  DELETE FROM org_members
  WHERE user_id = ANY(p_drop_uids)
    AND EXISTS (
      SELECT 1 FROM org_members km
      WHERE km.user_id = p_keep_uid AND km.org_id = org_members.org_id
    );
  UPDATE org_members SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);

  -- messages 通过 conversation_id 关联，不需要直接迁移
  -- credit_transactions / admin_action_logs 等其他 FK 表
  UPDATE credit_transactions SET user_id = p_keep_uid WHERE user_id = ANY(p_drop_uids);
  UPDATE admin_action_logs   SET target_user_id = p_keep_uid WHERE target_user_id = ANY(p_drop_uids);

  -- 删除 drop 用户（此时已无 FK 引用）
  DELETE FROM users WHERE id = ANY(p_drop_uids);

  RETURN jsonb_build_object(
    'keep_uid', p_keep_uid,
    'dropped', cardinality(p_drop_uids),
    'merged_credits', v_moved_credits,
    'merged_conversations', v_moved_convs,
    'merged_image_generations', v_moved_imgs,
    'merged_tasks', v_moved_tasks,
    'merged_credits_history', v_moved_credits_hist,
    'merged_mappings', v_moved_mappings
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION merge_wecom_duplicate_users IS
  '合并重复 wecom 用户：drop_uids 的所有 FK 引用迁到 keep_uid，累加积分';
