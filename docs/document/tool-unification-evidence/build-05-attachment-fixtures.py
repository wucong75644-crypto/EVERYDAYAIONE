"""Reproducible synthetic A/B inputs; original live calls required explicit user authorization."""
import argparse,copy,json,os,subprocess,sys,types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
os.chdir(ROOT)
parser=argparse.ArgumentParser(description="Build synthetic model fixtures; never call a model or execute tools.")
parser.add_argument("--output-dir",type=Path,required=True)
output=parser.parse_args().output_dir.resolve()
output.mkdir(parents=True,exist_ok=True)
assert not Path(".env").exists() and not Path("backend/.env").exists()
from services.handlers.chat_context import attachments,history_loader
from services.prompt_builder.builder import PromptBuilder
from services.prompt_builder.layers.user_layer import UserLayer,UserMessageInput
from services.prompt_builder.layers.static_layer import StaticLayer
from services.prompt_builder.layers.session_stable_layer import SessionStableLayer,SessionStableContext
from config.chat_tools import get_core_tools

def before_module(path,name,package=None,revision="887b28ae"):
    m=types.ModuleType(name)
    m.__package__=package
    sys.modules[name]=m
    exec(compile(subprocess.check_output(['git','show',revision+':'+path]).decode(),path,'exec'),m.__dict__)
    return m
old_attach=before_module('backend/services/handlers/chat_context/attachments.py','old_attach')
old_history=before_module('backend/services/handlers/chat_context/history_loader.py','old_history','services.handlers.chat_context')
old_tools=before_module('backend/config/file_tools.py','old_tools')
baseline_user=before_module('backend/services/prompt_builder/layers/user_layer.py','baseline_attachment_user',revision='1df0e01c')

def row(role,*parts):return {'role':role,'content':list(parts)}
def txt(s):return {'type':'text','text':s}
def file(name,path):return {'type':'file','name':name,'workspace_path':path,'mime_type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','size':6702}
oldfile=file('历史订单.xlsx','历史/历史订单.xlsx')
base=[row('user',txt('统计这份文件，生成汇总表和统计图'),oldfile),
row('assistant',txt('统计已完成。共 20 条记录。汇总表和统计图已生成。'),file('汇总表.xlsx','staging/汇总表.xlsx'),{'type':'chart','title':'历史订单统计'}),
row('user',txt('创建一个每天上午 9 点检查订单的定时任务')),
row('assistant',{'type':'form','form_id':'synthetic-form','form_type':'scheduled_task_create','title':'创建定时任务'}, {'type':'tool_step','tool_name':'manage_scheduled_task','tool_call_id':'synthetic-old-call','status':'completed'}),
row('user',txt('读取文件'),file('旧发票.xlsx','历史/旧发票.xlsx')),
row('assistant',txt('已读取旧发票，共 7 行，3 列。')),
row('user',txt('查询今天的付款订单数按照平台划分')),
row('assistant',txt('今天平台甲 10 单，平台乙 12 单。')),
row('user',txt('和昨天各平台的订单对比，看看涨跌幅')),
row('assistant',txt('平台甲昨天 5 单，今天上涨 100%；平台乙昨天 10 单，今天上涨 20%。'))]
workspace=file('测试分摊明细.xlsx','公摊/测试分摊明细.xlsx')
upload=file('测试平台订单.xlsx','上传/2026-09/测试平台订单.xlsx')
scenarios=[('workspace',base,workspace),('reupload',base+[row('user',txt('读取文件'),workspace),row('assistant',txt('请提供您要读取的文件。当前对话中未检测到新的附件文件。'))],upload)]
new_tools=get_core_tools()
old_file_tools={t['function']['name']:t for t in old_tools.build_file_tools()}
requests=[]
for scenario,rows,attached in scenarios:
 for variant in ('before','current','single_system'):
  mod=old_attach if variant=='before' else attachments
  hist_mod=old_history if variant=='before' else history_loader
  history=[]
  for r in rows: history.extend(hist_mod._row_to_oai_messages(r,0)[0])
  user=baseline_user.UserLayer.render(baseline_user.UserMessageInput(text='读取文件',workspace_files=[attached],attachments_xml=mod.format_attachments([attached],org_id='synthetic-org'),workspace_prompt=mod.build_workspace_prompt([attached],org_id='synthetic-org'),attachments_as_system=True))
  messages=PromptBuilder._compose_messages(StaticLayer.render(),SessionStableLayer.render(SessionStableContext()),'<turn><current_time>2026-09-11 10:00 UTC+8</current_time></turn>',history,None,user)
  tools=[copy.deepcopy(old_file_tools.get(t['function']['name'],t) if variant=='before' else t) for t in new_tools]
  if variant=='single_system':
   blocks=[]
   for m in messages:
    if m['role']=='system':
     c=m['content']
     if isinstance(c,str):blocks.append({'type':'text','text':c})
     else:blocks.extend(copy.deepcopy(c))
   messages=[{'role':'system','content':blocks}]+[m for m in messages if m['role']!='system']
  requests.append({'scenario':scenario,'variant':variant,'expected_file':attached['name'],'messages':messages,'tools':tools})
(output/'05-attachment-before-fixtures.json').write_text(json.dumps(requests,ensure_ascii=False,indent=2))
print('Prepared',len(requests),'synthetic requests;',len(new_tools),'tool descriptions; tools will never execute.')

import re,html
base=copy.deepcopy(requests)
requests=[]
for scenario in ('workspace','reupload'):
 for variant,origin in (('bound_native','current'),('bound_single_system','single_system'),('bound_long_history','current')):
  case=copy.deepcopy(next(c for c in base if c['scenario']==scenario and c['variant']==origin))
  serialized=json.dumps(case['messages'],ensure_ascii=False)
  # The original fixture's XML is synthetic and is the authoritative current file.
  joined='\n'.join(m['content'] if isinstance(m['content'],str) else '\n'.join(p.get('text','') for p in m['content']) for m in case['messages'] if m['role']=='system')
  path=html.unescape(re.findall(r'<path>(.*?)</path>',joined)[-1])
  expected_fid=re.findall(r'<id>(.*?)</id>',joined)[-1]
  attached={'name':case['expected_file'],'workspace_path':path}
  user=UserLayer.render(UserMessageInput(text='读取文件',workspace_files=[attached],org_id='synthetic-org'))
  refs=json.loads(user.user_message['content'][1]['text'].split('\n',1)[1])
  assert refs[0]['file_id']==expected_fid
  case['messages'][-1]=user.user_message
  if variant=='bound_long_history':
   extra=[]
   for i in range(6):
    extra.extend([{'role':'user','content':f'汇总历史报表 {i+1} 的订单。'}, {'role':'assistant','content':f'历史报表 {i+1} 已经完成，输出保存在 staging/旧统计{i+1}.xlsx。\n'+'\n'.join(f'合成渠道{j}：测试订单{j+10}笔，金额{j+20}元；历史汇总已完成，无待执行步骤。' for j in range(25))}])
   case['messages'][3:3]=extra
  case['variant']=variant
  requests.append(case)
(output/'05-attachment-binding-fixtures.json').write_text(json.dumps(requests,ensure_ascii=False,indent=2))

print("Candidate fixtures built without network or business data.")
