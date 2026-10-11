"""Read installed metadata inside the isolated runtime, without loading CLIs."""
import importlib.metadata as metadata
import json
import sys

result={}
for raw in sys.stdin.read(64000).splitlines():
    if raw and not raw.startswith('#'):
        name = raw.split('==',1)[0].split(' @ ',1)[0].strip()
        try:
            distribution=metadata.distribution(name)
            direct=json.loads(distribution.read_text('direct_url.json') or '{}')
            result[name]={'version':distribution.version,'commit':direct.get('vcs_info',{}).get('commit_id','')}
        except metadata.PackageNotFoundError:
            result[name]={'missing':True}
print(json.dumps(result))
