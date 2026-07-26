SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS update_governed_member_display_name(UUID, UUID, TEXT);

RESET ROLE;
