#!/usr/bin/env python3
"""Build script hashes for explicit admin review; never run deployment scripts."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def build():
    def digest(body):return hashlib.sha256(body).hexdigest()
    source=(ROOT/'deploy/deploy.sh').read_text()
    blocks=re.findall(r"remote_exec ([^\n]+) << 'ENDSSH'\n(.*?)^ENDSSH$",source,re.M|re.S)
    scripts={'deployment':[],'migrations':[],'storage':[],'python':[],'coordination':[]}
    for command,body in blocks:
        kind='deployment' if command=='bash' else 'migrations' if command.startswith('bash -s -- ') else None
        if kind: scripts[kind].append(digest(body.encode()))
    coord=(ROOT/'deploy/release-coordination.sh').read_text()
    body=re.search(r"<<'REMOTE_STATE'[^\n]*\n(.*?)^REMOTE_STATE$",coord,re.M|re.S).group(1)
    scripts['coordination']=[digest(body.encode())]
    scripts['storage']=[digest((ROOT/'deploy/ensure-skill-personal-storage.sh').read_bytes())]
    scripts['python']=[digest((ROOT/p).read_bytes()) for p in ('deploy/verify-production-database.py','deploy/verify_migration_executor.py','deploy/scheduled-task-drain.py')]
    return {'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'scripts':scripts}

if __name__=='__main__': print(json.dumps(build(),indent=2))
