"""Read-only research probe; synthetic data in an ephemeral loopback cluster."""
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_chat_v3 import v3_db, start


def test_search_behavior_and_evidence_boundary(v3_db):
    make, _ = v3_db
    repo = make()
    project = repo.command(command('create_project', {
        'title': '检索研究合成项目', 'position': {'x': 0, 'y': 0}}))
    project = repo.object(project['object_id'])
    result = repo.command(command('create_object', {
        'container_id': project['container_id'], 'parent_id': None,
        'kind': 'document', 'title': '售后政策', 'position': {'x': 50, 'y': 50},
        'body': '客户签收后七天内可以申请退货。商品型号 AB-1023 不支持刻字。'}))
    refs = [{'root': {'kind': 'container', 'id': str(project['container_id'])},
             'policy': 'latest_saved'}]
    run, _, _ = start(make, repo, refs)
    root = run.introduction()['roots'][0]['handle']
    queries = ['退货', '退货流程是什么', '退货 七天', '退回商品', 'AB-1023', 'AB1023']
    counts = {q: len(run.execute('search', {'root_handle': root, 'query': q})['items'])
              for q in queries}
    assert counts == {'退货': 1, '退货流程是什么': 0, '退货 七天': 0,
                      '退回商品': 0, 'AB-1023': 1, 'AB1023': 0}
    footer = run.evidence_footer()
    assert '尚未读取' in footer
    print('PROBE_RESULTS', counts)
    print('SNIPPET_ONLY_FINALIZATION', 'returns warning footer without exception')
