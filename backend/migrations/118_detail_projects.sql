-- 118: 主图详情页草稿项目与工作区图片引用
-- 只保存业务状态和 workspace_path；文件继续由 Workspace + OSS 管理。

CREATE TABLE IF NOT EXISTS detail_projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'analyzing', 'plan_ready', 'generating', 'completed', 'failed', 'archived')
    ),
    content_type TEXT NOT NULL DEFAULT 'main_image' CHECK (
        content_type IN ('main_image', 'detail_page')
    ),
    platform TEXT NOT NULL DEFAULT 'auto' CHECK (
        platform IN ('auto', 'taobao', 'tmall', 'jd', 'pdd')
    ),
    requirement TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh-CN' CHECK (language IN ('zh-CN', 'none')),
    aspect_ratio TEXT NOT NULL DEFAULT '1:1',
    quality TEXT NOT NULL DEFAULT '1k' CHECK (quality IN ('1k', '2k', '4k')),
    image_count SMALLINT NOT NULL DEFAULT 1 CHECK (image_count BETWEEN 1 AND 9),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS detail_project_images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES detail_projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_path TEXT NOT NULL CHECK (length(workspace_path) BETWEEN 1 AND 500),
    category TEXT NOT NULL CHECK (category IN ('product', 'reference')),
    sort_order SMALLINT NOT NULL CHECK (sort_order BETWEEN 0 AND 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, workspace_path),
    UNIQUE (project_id, sort_order)
);

CREATE INDEX IF NOT EXISTS idx_detail_projects_org_user_updated
    ON detail_projects(org_id, user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_detail_projects_personal_user_updated
    ON detail_projects(user_id, updated_at DESC) WHERE org_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_detail_projects_org_draft
    ON detail_projects(user_id, org_id)
    WHERE status = 'draft' AND org_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_detail_projects_personal_draft
    ON detail_projects(user_id)
    WHERE status = 'draft' AND org_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_detail_project_images_order
    ON detail_project_images(project_id, sort_order);

CREATE OR REPLACE FUNCTION attach_detail_project_image(
    p_user_id UUID,
    p_org_id UUID,
    p_workspace_path TEXT,
    p_category TEXT
)
RETURNS TABLE(project_id UUID, project_version INTEGER, image_id UUID)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_project_id UUID;
    v_version INTEGER;
    v_image_id UUID;
    v_count INTEGER;
    v_sort_order SMALLINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE id = p_user_id) THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_USER_NOT_FOUND' USING ERRCODE = '23503';
    END IF;
    IF p_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM org_members member
         WHERE member.org_id = p_org_id
           AND member.user_id = p_user_id
           AND member.status = 'active'
    ) THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_ORG_ACCESS_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_workspace_path IS NULL OR length(p_workspace_path) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_PATH' USING ERRCODE = '22023';
    END IF;
    IF p_category NOT IN ('product', 'reference') THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_CATEGORY' USING ERRCODE = '22023';
    END IF;

    SELECT id, version
      INTO v_project_id, v_version
      FROM detail_projects
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'draft'
     FOR UPDATE;

    IF v_project_id IS NULL THEN
        BEGIN
            INSERT INTO detail_projects(user_id, org_id)
            VALUES (p_user_id, p_org_id)
            RETURNING id, version INTO v_project_id, v_version;
        EXCEPTION WHEN unique_violation THEN
            SELECT id, version
              INTO v_project_id, v_version
              FROM detail_projects
             WHERE user_id = p_user_id
               AND org_id IS NOT DISTINCT FROM p_org_id
               AND status = 'draft'
             FOR UPDATE;
        END;
    END IF;

    IF v_project_id IS NULL THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_CREATE_FAILED' USING ERRCODE = 'P0001';
    END IF;

    IF EXISTS (
        SELECT 1 FROM detail_project_images image
         WHERE image.project_id = v_project_id
           AND image.workspace_path = p_workspace_path
    ) THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_DUPLICATE' USING ERRCODE = '23505';
    END IF;

    SELECT COUNT(*), COALESCE(MAX(sort_order) + 1, 0)::SMALLINT
      INTO v_count, v_sort_order
      FROM detail_project_images image
     WHERE image.project_id = v_project_id;

    IF v_count >= 9 THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_LIMIT_EXCEEDED' USING ERRCODE = '22023';
    END IF;

    INSERT INTO detail_project_images(
        project_id, user_id, org_id, workspace_path, category, sort_order
    ) VALUES (
        v_project_id, p_user_id, p_org_id, p_workspace_path, p_category, v_sort_order
    ) RETURNING id INTO v_image_id;

    UPDATE detail_projects
       SET version = version + 1, updated_at = NOW()
     WHERE id = v_project_id
     RETURNING version INTO v_version;

    RETURN QUERY SELECT v_project_id, v_version, v_image_id;
END;
$$;

REVOKE ALL ON FUNCTION attach_detail_project_image(UUID, UUID, TEXT, TEXT) FROM PUBLIC;

COMMENT ON TABLE detail_projects IS '主图详情页制作项目；第一阶段只使用 draft 状态';
COMMENT ON TABLE detail_project_images IS '项目对 Workspace 图片的业务引用，不拥有文件生命周期';
COMMENT ON FUNCTION attach_detail_project_image(UUID, UUID, TEXT, TEXT)
    IS '校验用户/企业归属，原子获取或创建草稿并关联最多9张工作区图片';

-- 回滚顺序（确认无业务引用后执行）：
-- DROP FUNCTION IF EXISTS attach_detail_project_image(UUID, UUID, TEXT, TEXT);
-- DROP TABLE IF EXISTS detail_project_images;
-- DROP TABLE IF EXISTS detail_projects;
