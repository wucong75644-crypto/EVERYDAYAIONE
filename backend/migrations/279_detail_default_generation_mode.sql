-- 默认套图模式：7张主图 + 7张详情图。保留现有草稿设置。
BEGIN;
ALTER TABLE detail_projects DROP CONSTRAINT IF EXISTS detail_projects_content_type_check;
ALTER TABLE detail_projects DROP CONSTRAINT IF EXISTS detail_projects_image_count_check;
ALTER TABLE detail_projects ADD CONSTRAINT detail_projects_content_type_check
    CHECK (content_type IN ('default', 'main_image', 'detail_page'));
ALTER TABLE detail_projects ADD CONSTRAINT detail_projects_image_count_check
    CHECK ((content_type = 'default' AND image_count = 14)
        OR (content_type IN ('main_image', 'detail_page') AND image_count BETWEEN 1 AND 9));
ALTER TABLE detail_projects ALTER COLUMN content_type SET DEFAULT 'default';
ALTER TABLE detail_projects ALTER COLUMN image_count SET DEFAULT 14;
COMMIT;
