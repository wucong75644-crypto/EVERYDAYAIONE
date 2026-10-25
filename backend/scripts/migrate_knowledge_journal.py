"""Explicit offline import. Default is a read-only review; never loads deploy credentials.

Run with an operator-provided DATABASE_URL and --actor/--org UUIDs. Review first;
--apply-reviewed REPORT requires exactly the unchanged report from the dry run.
Only user:<actor> experience nodes can be imported, always as candidate methods.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg.rows import dict_row
from core.db_scope import DatabaseScope, DatabaseAccessKind, SET_DATABASE_SCOPE_SQL
from services.knowledge_workspace.legacy_migration import review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor', required=True, type=UUID)
    parser.add_argument('--org', type=UUID)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--apply-reviewed', type=Path)
    args = parser.parse_args()
    if args.apply_reviewed and args.report.resolve() == args.apply_reviewed.resolve():
        parser.error('--report must differ from --apply-reviewed')
    if args.report.exists():
        parser.error('--report already exists; choose a new report path')
    actor, org = str(args.actor), str(args.org) if args.org else None
    scope = DatabaseScope(actor, org, DatabaseAccessKind.RUNTIME)
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as db:
        with db.transaction(), db.cursor() as c:
            if not args.apply_reviewed:
                c.execute('SET TRANSACTION READ ONLY')
            c.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
            c.execute('''SELECT id,category,title,content,scope,org_id,source,is_deleted,updated_at,
              md5(jsonb_build_array(id,title,content,scope,org_id,category,source,is_deleted,updated_at)::text) AS fingerprint
              FROM public.knowledge_nodes WHERE org_id IS NOT DISTINCT FROM %s::uuid ORDER BY id''', (org,))
            nodes = [{**row, 'org_id': str(row['org_id']) if row['org_id'] else None} for row in c.fetchall()]
            report = {'actor': actor, 'org': org, 'items': review(nodes, actor, org)}
            if args.apply_reviewed:
                approved = json.loads(args.apply_reviewed.read_text())
                if approved != report:
                    raise RuntimeError('SOURCE_OR_SCOPE_CHANGED_REVIEW_AGAIN')
                for item in report['items']:
                    if item['decision'] == 'import_candidate_method':
                        source = next(n for n in nodes if str(n['id']) == item['id'])
                        c.execute('SELECT public.kb_ai_import_entry(%s,%s) AS result', (item['id'], source['fingerprint']))
                        item['result'] = c.fetchone()['result']
                        if item['result']['status'] == 'source_changed':
                            raise RuntimeError('SOURCE_CHANGED_REVIEW_AGAIN')
        # Report only after successful transaction commit. Never overwrite the
        # reviewed input with import statuses; it remains reusable for idempotency.
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    os.chmod(args.report, 0o600)
    print(f"Reviewed {len(report['items'])} records; report written.")


if __name__ == '__main__':
    main()
