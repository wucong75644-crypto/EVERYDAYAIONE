DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
DO $roles$
BEGIN
    IF to_regrole('everydayai_owner') IS NULL
        THEN CREATE ROLE everydayai_owner NOLOGIN; END IF;
    IF to_regrole('everydayai_runtime') IS NULL
        THEN CREATE ROLE everydayai_runtime NOLOGIN; END IF;
    IF to_regrole('everydayai_wecom_runtime') IS NULL
        THEN CREATE ROLE everydayai_wecom_runtime NOLOGIN; END IF;
    IF to_regrole('everydayai_worker') IS NULL
        THEN CREATE ROLE everydayai_worker NOLOGIN; END IF;
    IF to_regrole('everydayai_sync') IS NULL
        THEN CREATE ROLE everydayai_sync NOLOGIN; END IF;
    IF to_regrole('everydayai') IS NULL
        THEN CREATE ROLE everydayai NOLOGIN; END IF;
END
$roles$;
GRANT everydayai_owner, everydayai_runtime, everydayai_wecom_runtime,
      everydayai_worker, everydayai_sync, everydayai TO CURRENT_USER;
GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner;
GRANT USAGE ON SCHEMA public TO everydayai_runtime,
    everydayai_wecom_runtime, everydayai_worker;
SET ROLE everydayai_owner;
CREATE TABLE users(id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE organizations(
    id UUID PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE org_members(
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active', PRIMARY KEY(org_id, user_id)
);
CREATE TABLE conversations(
    id UUID PRIMARY KEY, user_id UUID REFERENCES users(id),
    org_id UUID REFERENCES organizations(id), scope_type TEXT NOT NULL,
    scope_id TEXT
);
CREATE FUNCTION tenant_actor_user_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN pg_input_is_valid(current_setting(
            'app.actor_user_id', TRUE), 'uuid')
        THEN current_setting('app.actor_user_id', TRUE)::UUID
    END
$$;
CREATE FUNCTION tenant_org_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN pg_input_is_valid(current_setting('app.org_id', TRUE), 'uuid')
        THEN current_setting('app.org_id', TRUE)::UUID
    END
$$;
INSERT INTO users(id) VALUES ('11111111-1111-1111-1111-111111111111'),
    ('44444444-4444-4444-4444-444444444444'),
    ('77777777-7777-7777-7777-777777777777');
INSERT INTO organizations(id)
VALUES ('22222222-2222-2222-2222-222222222222');
INSERT INTO org_members(org_id, user_id) VALUES
    ('22222222-2222-2222-2222-222222222222',
     '44444444-4444-4444-4444-444444444444'),
    ('22222222-2222-2222-2222-222222222222',
     '77777777-7777-7777-7777-777777777777');
INSERT INTO conversations(id, user_id, org_id, scope_type, scope_id) VALUES
    ('33333333-3333-3333-3333-333333333333',
     '11111111-1111-1111-1111-111111111111',
     NULL, 'user', '11111111-1111-1111-1111-111111111111'),
    ('55555555-5555-5555-5555-555555555555',
     '44444444-4444-4444-4444-444444444444',
     '22222222-2222-2222-2222-222222222222',
     'user', '44444444-4444-4444-4444-444444444444'),
    ('66666666-6666-6666-6666-666666666666', NULL,
     '22222222-2222-2222-2222-222222222222', 'channel', 'wecom:group:test');
RESET ROLE;
