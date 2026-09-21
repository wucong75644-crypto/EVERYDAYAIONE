"""Deletion is a retained tombstone, permitted only after dependency checks."""


def check_deletion(cursor, package, draft):
    if not draft or draft['status'] != 'deprecated':
        return {'allowed': False, 'reason': 'SKILL_DEPRECATION_REQUIRED', 'blocking_tasks': 0, 'uncertain_tasks': 0}
    cursor.execute('SELECT public.skill_deletion_blockers(%s, %s) AS blockers', (package.id, package.skill_key))
    result = cursor.fetchone()['blockers']
    reason = ('SKILL_DELETE_CHECK_UNCERTAIN' if result['uncertain_tasks'] else
              'SKILL_DELETE_IN_USE' if result['blocking_tasks'] else None)
    return {'allowed': reason is None, 'reason': reason, **result}
