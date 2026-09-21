"""Undo only the current audited suspension; never republish or grant access."""

from services.skills.contracts import PublishRevision, SkillError, SkillRevision


def reenable(cursor, package, draft, storage_factory, *, deprecating=False):
    if not draft or draft['status'] != 'disabled':
        raise SkillError('SKILL_TRANSITION_INVALID')
    cursor.execute('SELECT public.skill_disabled_restore_state(%s, %s) AS status',
                   (package.id, draft['version']))
    status = cursor.fetchone()['status']
    if status not in ('draft', 'in_review', 'published', 'deprecated'):
        raise SkillError('SKILL_TRANSITION_INVALID')

    if deprecating:
        status = 'deprecated'

    cursor.execute('''SELECT r.*, public.skill_disabled_restore_state(r.package_id, %s, r.id) AS restore_status
        FROM public.skill_revisions r WHERE r.package_id = %s AND r.status = 'disabled'
        ORDER BY r.id FOR UPDATE''', (draft['version'], package.id))
    revisions = [row for row in cursor.fetchall() if row['restore_status'] in ('published', 'deprecated')]
    # Read every affected immutable file before changing any state. A missing or
    # changed historical file must also prevent silently reopening old Turns.
    storage = storage_factory() if revisions else None
    for row in revisions:
        saved = SkillRevision.model_validate({key: value for key, value in row.items() if key != 'restore_status'})
        storage.validate(package, PublishRevision(
            revision=saved.revision, content_sha256=saved.content_sha256,
            body_sha256=saved.body_sha256), nas_path=saved.nas_path)
    for row in revisions:
        cursor.execute('UPDATE public.skill_revisions SET status = %s WHERE id = %s',
                       ('deprecated' if deprecating else row['restore_status'], row['id']))
    cursor.execute('''UPDATE public.skill_drafts SET status = %s, version = version + 1
        WHERE package_id = %s''', (status, package.id))
