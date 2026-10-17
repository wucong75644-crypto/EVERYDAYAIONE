#!/usr/bin/env python3
"""Export an audited new build as a candidate baseline, never overwrite a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from catalog import catalog,fingerprint

ROOT=Path(__file__).resolve().parents[3]
NATIVE=Path('/workspace/.everydayaione-dev')
HERE=Path(__file__).resolve().parent


def normalize_deparsed(value):
    # PG17 reparses pg_dump's varchar-array -> text-array casts as element casts.
    # This is the only accepted source/restore expression normalization.
    if isinstance(value,str):
        value=re.sub(r'\(ARRAY\[(.*?)\]\)::text\[\]',lambda m:'ARRAY['+m[1].replace('::character varying','::text')+']',value)
        return re.sub(r"\(('(?:[^']|'')*')::character varying\)::text",r'\1::text',value)
    if isinstance(value,list):return [normalize_deparsed(x) for x in value]
    if isinstance(value,dict):return {k:normalize_deparsed(v) for k,v in value.items()}
    return value


def export(name,output):
    report=json.loads((ROOT/'.cloud-dev/builds'/name/'report.json').read_text())
    if report['database']!=name or report['status']!='built':raise RuntimeError('A completed private source build is required')
    output=output.resolve()
    if not output.is_relative_to(ROOT/'.cloud-dev'):raise RuntimeError('Export only to a new ignored candidate directory')
    output.mkdir(parents=True,exist_ok=False)
    m=json.loads((NATIVE/'instances'/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]/'local.json').read_text())
    conn=dict(host='127.0.0.1',port=m['pgport'],user='dev_admin',password=m['password'])
    with psycopg.connect(**conn,dbname=name) as c:
        expected=NATIVE/'instances'/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]/'pgdata'
        if Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()!=expected.resolve():raise RuntimeError('Private cluster identity mismatch')
        source=catalog(c)
        nonempty=[]
        for (table,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall():
            count=c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('public',table))).fetchone()[0]
            if count:nonempty.append((table,count))
        if {t for t,_ in nonempty}!={'configuration_bundle_definitions','configuration_definitions','permissions'}:
            raise RuntimeError('Build contains unapproved data: only repository registry seed metadata may export')
    env=dict(os.environ,PGPASSWORD=m['password'],LD_LIBRARY_PATH=str(NATIVE/'runtime/usr/lib/x86_64-linux-gnu'))
    pg=NATIVE/'runtime/usr/lib/postgresql/17/bin/pg_dump'
    args=[str(pg),'-h','127.0.0.1','-p',str(m['pgport']),'-U','dev_admin','-d',name]
    for filename,options in [('schema.sql',['--schema-only']),('seed.sql',['--data-only','--inserts','--column-inserts',*[f'--table=public.{t}' for t,_ in nonempty]])]:
        text=subprocess.check_output(args+options,env=env,text=True)
        text='\n'.join(line.rstrip() for line in text.splitlines() if not line.startswith(('\\restrict ','\\unrestrict '))).rstrip()+'\n'
        (output/filename).write_text(text)
    # Verify a genuinely second empty database, retaining it for audit.
    target='cloud_dev_export_'+uuid4().hex
    with psycopg.connect(**conn,dbname='postgres',autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
    with psycopg.connect(**conn,dbname=target) as c:
        c.execute((output/'schema.sql').read_text());c.execute((output/'seed.sql').read_text())
        restored=catalog(c)
        if normalize_deparsed(source)!=normalize_deparsed(restored):
            raise RuntimeError('Export/restore changed the schema or access checks')
    (output/'catalog.json').write_text(json.dumps(restored,indent=2)+'\n')
    for name in ['native-prerequisites.sql','094-predecessors.sql']:
        (output/name).write_bytes((HERE/name).read_bytes())
    report.pop('database')
    report.update(bootstrap_identity='native-development-v1',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        catalog_fingerprint=fingerprint(restored),source_catalog_fingerprint=fingerprint(source),seed_tables=nonempty,
        builder_sha256=hashlib.sha256((HERE/'build.py').read_bytes()).hexdigest(),
        original_source_checksums=json.loads((HERE/'manifest.json').read_text())['original_source_checksums'])
    report['artifacts']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    report['restore_normalization']='Only PG17 varchar-array -> text-array deparser cast distribution; every other catalog/ACL field matches.'
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Audited baseline candidate exported and independently restored:',output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('database');p.add_argument('output',type=Path)
    a=p.parse_args();export(a.database,a.output)
