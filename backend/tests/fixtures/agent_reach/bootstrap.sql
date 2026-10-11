CREATE ROLE everydayai_owner;
CREATE ROLE everydayai;
CREATE ROLE everydayai_runtime;
SELECT format('GRANT everydayai_owner,everydayai TO %I', current_user) \gexec
CREATE TABLE organizations(id uuid primary key,status text);
CREATE TABLE users(id uuid primary key,status text);
CREATE TABLE org_members(org_id uuid,user_id uuid,role text,status text);
ALTER TABLE organizations OWNER TO everydayai;
ALTER TABLE users OWNER TO everydayai;
ALTER TABLE org_members OWNER TO everydayai;
GRANT USAGE,CREATE ON SCHEMA public TO everydayai_owner,everydayai;
GRANT SELECT ON organizations,users,org_members TO everydayai_owner,everydayai_runtime;
INSERT INTO organizations VALUES ('00000000-0000-0000-0000-000000000001','active'),('00000000-0000-0000-0000-000000000002','active');
INSERT INTO users VALUES ('00000000-0000-0000-0000-000000000011','active'),('00000000-0000-0000-0000-000000000012','active');
INSERT INTO org_members VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000011','owner','active'),('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000012','owner','active');
