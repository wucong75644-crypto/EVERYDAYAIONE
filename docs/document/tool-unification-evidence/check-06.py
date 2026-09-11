"""Verify block-06 scope, unchanged public/Actor contracts, and exact source identities."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[3]
base='cdba58f9018ff45be2ebde0b802471ca634d8727'
def git(*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def old(path): return subprocess.check_output(['git','show',f'{base}:{path}'],cwd=root)
assert git('branch','--show-current').startswith('codex/task/')
assert git('rev-parse',base+'^{tree}')==git('rev-parse','87b07718^{tree}')
for commit in ['b4c854ac','4084db4e','2ed4d783','1f288018','87b07718']:
 subprocess.run(['git','merge-base','--is-ancestor',commit,base],cwd=root,check=True)
unchanged=[
 'backend/services/agent/agent_result.py','backend/services/agent/tool_output.py',
 'backend/schemas/multimodal.py','backend/schemas/websocket.py','backend/schemas/message.py',
 'backend/services/handlers/emit_payloads.py','backend/services/handlers/chat/actor_sink.py',
 'backend/services/handlers/chat/execution_sink.py','backend/services/handlers/chat/execution_engine.py',
 'backend/services/agent/scheduled_task_agent.py','backend/services/media_tool_executor.py',
 'backend/services/sandbox/executor.py','backend/services/agent/erp_agent.py',
 'backend/services/tools/legacy.py','backend/services/tools/legacy_policy.py',
 'backend/services/tools/policy.py','backend/services/tools/registry.py','backend/services/tools/spec.py',
 'backend/config/chat_tools.py','backend/config/tool_domains.py',
 'backend/services/agent/tool_executor.py','backend/services/tool_executor.py',
 'backend/services/agent/stop_policy.py','backend/services/conversation_runtime.py','backend/services/conversation_worker.py',
 'backend/services/conversation_state.py','backend/services/conversation_turn_runtime.py',
]
for path in unchanged: assert (root/path).read_bytes()==old(path),path
ledger='backend/services/tool_invocation_store.py'
def node(data,name): return next(n for n in ast.parse(data).body if getattr(n,'name',None)==name)
for name in ['ToolInvocationStore','DatabaseToolInvocationStore','hash_tool_arguments']:
 assert ast.dump(node(old(ledger),name))==ast.dump(node((root/ledger).read_bytes(),name)),name
changed=git('diff','--name-only').splitlines()+git('ls-files','--others','--exclude-standard').splitlines()
assert all(p.startswith(('backend/core/','backend/services/','backend/tests/','docs/document/')) for p in changed)
assert not any(p.startswith('backend/migrations/') for p in changed)
source=sorted(p for p in changed if p.startswith(('backend/core/','backend/services/','backend/tests/')))
checksums={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source}
print(json.dumps({'base':base,'head':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),
 'tested_version':'HEAD plus working-tree sources listed below; no candidate commit yet',
 'prerequisites_01_05_in_main':True,'same_tree_as_05_final':'87b07718',
 'unchanged_boundaries':unchanged,'actor_store_rpc_and_hash_unchanged':True,'database_schema_unchanged':True,
 'source_identity':hashlib.sha256(json.dumps(checksums,sort_keys=True).encode()).hexdigest(),
 'fingerprints':checksums},ensure_ascii=False,indent=2))
