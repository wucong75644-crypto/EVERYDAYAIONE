import asyncio,json,tempfile
from pathlib import Path
import pytest
from services.agent.agent_result import AgentResult
from tests.test_tool_production_integration import setup,tc
import tests.test_tool_result_consumption as consumer

async def main():
    reports=[]
    with tempfile.TemporaryDirectory(prefix='tool07-pause-repro-') as temporary:
        root=Path(temporary)
        for transport in ['legacy','actor']:
            with pytest.MonkeyPatch.context() as mp:
                setup.__wrapped__(mp,root)
                proposal={'id':'change-test','resource_type':'scheduled_task','resource_id':'task-test','operation':'pause','status':'awaiting_approval'}
                raw=AgentResult('暂停方案已生成，请确认后提交。',status='success',metadata={'change_set':proposal})
                mp.setattr(consumer,'sample',lambda case,root:(tc('manage_scheduled_task',{'action':'pause','task_name':'测试任务'}),raw))
                actual,results,host,executor,_=await consumer.chat_run('pause',transport,root,mp)
                blocks=actual['checkpoint']['content_blocks']
                cards=[b for b in blocks if b.get('type')=='changeset']
                report={'transport':transport,'metadata_retained':results[0][1].metadata.get('change_set')==proposal,'handler_calls':executor.handler.await_count,'block_types':[b['type'] for b in blocks],'confirmation_cards':len(cards),'terminal_form':bool(getattr(host,'_terminal_form_pending',False)),'model_message':actual['checkpoint']['messages'][-1]}
                assert report['metadata_retained'] and report['handler_calls']==1
                assert report['confirmation_cards']==0 and not report['terminal_form']
                reports.append(report)
    print(json.dumps({'reproduced_missing_confirmation_card':True,'reports':reports},ensure_ascii=False,indent=2))
asyncio.run(main())
