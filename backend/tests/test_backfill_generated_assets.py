from unittest.mock import MagicMock, patch
from scripts.backfill_generated_assets import backfill, task_parts


def database(refs=None):
    task = {'id': 'task-id', 'type': 'image', 'user_id': 'user', 'result_data': {'type': 'image', 'url': 'saved'}}
    db = MagicMock()
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.in_.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.execute.return_value.data = [task]
    ref_query = MagicMock()
    ref_query.select.return_value = ref_query
    ref_query.eq.return_value = ref_query
    ref_query.execute.return_value.data = refs or []
    db.table.side_effect = lambda name: query if name == 'tasks' else ref_query
    return db


def test_default_is_read_only():
    with patch('scripts.backfill_generated_assets.register_task_media_best_effort') as register:
        stats = backfill(database(), limit=1)
    register.assert_not_called()
    assert stats == dict(scanned=1, missing=1, registered=0, failed=0)


def test_apply_skips_registered_task():
    with patch('scripts.backfill_generated_assets.register_task_media_best_effort') as register:
        stats = backfill(database([{'ref_key': 'task:task-id:0'}]), apply=True, limit=1)
    register.assert_not_called()
    assert stats['missing'] == 0


def test_apply_reports_registration_failure():
    with patch('scripts.backfill_generated_assets.register_task_media_best_effort', return_value=[]):
        stats = backfill(database(), apply=True, limit=1)
    assert stats['missing'] == stats['failed'] == 1


def test_batch_message_fallback_selects_owned_image():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        'content': [{'type': 'image', 'url': 'first'}, {'type': 'image', 'url': 'second'}]}
    assert task_parts(db, {'batch_id': 'batch', 'image_index': 1, 'placeholder_message_id': 'msg'}) == [{'type': 'image', 'url': 'second'}]
