"""Canonical public application catalog, independent of object OIDs/data."""
import hashlib
import json


def catalog(connection):
    with connection.cursor() as c:
        c.execute("SELECT set_config('search_path','pg_catalog',true)")
        queries = {
            'relations': """SELECT relname,relkind::text,pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity
                FROM pg_class WHERE relnamespace='public'::regnamespace
                AND relkind IN ('r','p','v','m','S') ORDER BY relname""",
            'columns': """SELECT r.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid
                LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE r.relnamespace='public'::regnamespace AND r.relkind IN ('r','p','v','m')
                AND a.attnum>0 AND NOT a.attisdropped ORDER BY r.relname,a.attnum""",
            'constraints': """SELECT r.relname,k.conname,pg_get_constraintdef(k.oid)
                FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid
                WHERE r.relnamespace='public'::regnamespace ORDER BY r.relname,k.conname""",
            'functions': """SELECT p.oid::regprocedure::text,pg_get_userbyid(p.proowner),p.prosecdef,
                p.provolatile::text,p.prosrc,pg_get_function_result(p.oid),p.proconfig
                FROM pg_proc p WHERE p.pronamespace='public'::regnamespace AND p.prokind='f'
                AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                    AND d.objid=p.oid AND d.deptype='e') ORDER BY 1""",
            'policies': """SELECT c.relname,p.polname,p.polcmd::text,p.polpermissive,
                ARRAY(SELECT CASE WHEN role_oid=0 THEN 'PUBLIC' ELSE pg_get_userbyid(role_oid)::text END
                    FROM unnest(p.polroles) role_oid ORDER BY 1),
                pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                WHERE c.relnamespace='public'::regnamespace ORDER BY c.relname,p.polname""",
            'table_privileges': """SELECT r.relname,
                CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee)::text END,
                a.privilege_type,a.is_grantable FROM pg_class r
                CROSS JOIN LATERAL aclexplode(COALESCE(r.relacl,acldefault(
                    CASE WHEN r.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,r.relowner))) a
                WHERE r.relnamespace='public'::regnamespace AND r.relkind IN ('r','p','v','m','S')
                ORDER BY 1,2,3,4""",
            'function_privileges': """SELECT p.oid::regprocedure::text,
                CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee)::text END,
                a.privilege_type,a.is_grantable FROM pg_proc p
                CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                WHERE p.pronamespace='public'::regnamespace AND p.prokind='f'
                AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                    AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2,3,4""",
        }
        result={key:[list(row) for row in c.execute(query).fetchall()] for key,query in queries.items()}
        for row in result['functions']:
            row[4]=hashlib.sha256(row[4].encode()).hexdigest()
        return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
