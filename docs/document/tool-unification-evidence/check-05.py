"""Scope, unchanged public boundaries and fingerprints for block 05."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[3]
base='6c0737ab78f2d0cb3b2a8376498e7825b0829431'
def git(*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def old(path): return subprocess.check_output(['git','show',f'{base}:{path}'],cwd=root)
assert git('branch','--show-current').startswith('codex/task/')
assert git('rev-parse',base+'^{tree}')==git('rev-parse','1f288018^{tree}')
unchanged=[
 'backend/services/agent/agent_result.py','backend/services/agent/tool_output.py',
 'backend/schemas/multimodal.py','backend/schemas/websocket.py','backend/schemas/message.py',
 'backend/services/handlers/emit_payloads.py','backend/services/handlers/chat/actor_sink.py',
 'backend/services/handlers/chat/execution_sink.py','backend/services/agent/scheduled_task_agent.py',
 'backend/services/media_tool_executor.py','backend/services/sandbox/executor.py',
 'backend/services/tools/runtime.py','backend/services/tools/policy.py','backend/services/tools/registry.py',
 'backend/config/chat_tools.py','backend/config/tool_domains.py',
]
for path in unchanged: assert (root/path).read_bytes()==old(path),path
ledger='backend/services/tool_invocation_store.py'
old_ast=ast.parse(old(ledger));new_ast=ast.parse((root/ledger).read_bytes())
def func(tree,name): return next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
assert ast.dump(func(old_ast,'deserialize_tool_result'))==ast.dump(func(new_ast,'deserialize_tool_result'))
writer=func(new_ast,'serialize_tool_result')
writer.body=[n for n in writer.body if not (
 isinstance(n,ast.ImportFrom) and n.module=='services.tools.result'
 or isinstance(n,ast.If) and ast.unparse(n.test)=='isinstance(result, ToolResult)'
)]
assert ast.dump(func(old_ast,'serialize_tool_result'))==ast.dump(writer)
changed=git('diff','--name-only').splitlines()+git('ls-files','--others','--exclude-standard').splitlines()
assert all(p.startswith(('backend/services/','backend/tests/','docs/document/')) for p in changed)
source=[p for p in changed if p.startswith(('backend/services/','backend/tests/'))]
print(json.dumps({'base':base,'head':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),
 'tested_tree':'HEAD plus uncommitted files listed below; not a deployed SHA',
 'unchanged_boundaries':unchanged,'legacy_reader_unchanged':True,'legacy_writer_only_adds_envelope_rejection':True,
 'fingerprints':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in sorted(source)}},ensure_ascii=False,indent=2))
