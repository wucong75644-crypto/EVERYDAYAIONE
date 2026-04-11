-- 070: 新增权限点 task.push_to_others（推送给他人/群聊）
-- 背景: 普通员工（默认 member 职位）只能创建"推送给自己"的任务,
--       推送给同事或群聊需要管理职位（boss/vp/manager/deputy）。
--
-- 所属模块: task
-- 角色映射在 services/permissions/permission_points.py 同步维护
-- (V1 只用代码层硬编码 + initialization.py 灌入 role_permissions)

INSERT INTO permissions (code, module, action, name, description) VALUES
    ('task.push_to_others', 'task', 'push_to_others',
     '推送任务给他人',
     '创建定时任务时把推送目标设为同事或群聊（员工默认只能推给自己）')
ON CONFLICT (code) DO NOTHING;
