"""Offline review of legacy nodes. No org/global content becomes a personal memory."""
import hashlib
import json
from .journal_privacy import SENSITIVE


def classify(node, actor_id, org_id):
    if node.get('is_deleted'):
        return 'archive_deleted'
    if node.get('category') != 'experience':
        return 'archive_configuration'
    if node.get('org_id') != org_id or node.get('scope') != 'user:' + actor_id:
        return 'review_ownership'
    if not node.get('content', '').strip() or len(node['content']) > 5000:
        return 'review_content'
    if SENSITIVE.search(node.get('title', '') + '\n' + node['content']):
        return 'review_sensitive'
    return 'import_candidate_method'


def review(nodes, actor_id, org_id):
    result = []
    for node in nodes:
        # Review output deliberately contains no plaintext personal content.
        result.append({'id': str(node['id']), 'decision': classify(node, actor_id, org_id),
                       'source': node.get('source'),
                       'sha256': hashlib.sha256(json.dumps(node, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()})
    return result
