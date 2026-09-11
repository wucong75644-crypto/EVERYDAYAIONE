"""Verify writer-on integration and fresh-process recovery using isolated resources."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
evidence = Path(__file__).resolve().parent
assert not (root / '.env').exists() and not (root / 'backend/.env').exists()
env = dict(os.environ, APP_ENV='testing', DATABASE_URL='postgresql://tool_test:tool_test@127.0.0.1:1/tool_test',
           JWT_SECRET_KEY='tool-unification-test-only', REDIS_PORT='1',
           PYTHONPATH='/private/tmp/tool05-testdeps:' + str(root / 'backend'),
           MPLCONFIGDIR='/private/tmp/tool06-matplotlib', IPYTHONDIR='/private/tmp/tool06-ipython')
results = []
with tempfile.TemporaryDirectory(prefix='tool06-process-ledger-') as folder:
    for status in ('success', 'error'):
        for phase, version in (('write', '1'), ('read', '0')):
            env['TOOL_RESULT_PAYLOAD_WRITE_VERSION'] = version
            run = subprocess.run([sys.executable, str(evidence / 'process-recovery-06.py'), phase, status, folder],
                                 cwd=root, env=env, text=True, capture_output=True)
            if run.returncode:
                print(run.stdout + run.stderr)
                raise SystemExit(run.returncode)
            result = json.loads(run.stdout.strip().splitlines()[-1])
            results.append(result)
            print(json.dumps(result))
(evidence / '06-writer-process-recovery.json').write_text(json.dumps(results, indent=2))
