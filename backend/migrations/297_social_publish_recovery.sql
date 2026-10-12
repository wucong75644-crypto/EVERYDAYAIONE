-- Preserve settled attempts; only explicit human non-publication attestation frees a slot.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE social_publish_intents DROP CONSTRAINT social_publish_intents_state_check;
ALTER TABLE social_publish_intents ADD CONSTRAINT social_publish_intents_state_check CHECK(state IN ('awaiting_confirmation','dispatching','submitted','verified','uncertain','failed','cancelled','invalidated','not_published'));
DO $$ DECLARE constraint_name text; BEGIN
 SELECT conname INTO STRICT constraint_name FROM pg_constraint
 WHERE conrelid='social_publish_intents'::regclass AND contype='u'
 AND pg_get_constraintdef(oid)='UNIQUE (org_id, content_id, revision, connection_id)';
 EXECUTE format('ALTER TABLE social_publish_intents DROP CONSTRAINT %I',constraint_name);
END $$;
CREATE UNIQUE INDEX social_publish_active_version ON social_publish_intents(org_id,content_id,revision,connection_id) WHERE state<>'not_published';
RESET ROLE;
