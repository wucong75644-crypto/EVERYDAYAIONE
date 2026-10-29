"""Synthetic extraction contracts; no provider credential or private training data."""
import asyncio
import io
import json
import httpx
import pytest
from PIL import Image
from services.knowledge_workspace import derivation,media


def test_lossless_detail_views_cover_original_and_bound_size():
    views=media.image_views(Image.new('RGB',(1085,627),'white'))
    assert len(views)==7 and views[0][0]=='原图'
    assert any('540,440' in label for label,_ in views)
    for _,data in views:
        assert data.startswith(b'\x89PNG')
        with Image.open(io.BytesIO(data)) as view:assert max(view.size)<=1800
    assert len(media.image_views(Image.new('1',(4000,4000))))<=9


def test_ocr_keeps_literal_fields_and_reauthorizes(monkeypatch):
    calls=[];authorized=[]
    async def respond(request):
        body=json.loads(request.content);calls.append(body)
        assert authorized==[True]
        assert body['enable_thinking'] is False and body['temperature']==0
        assert '不能按语义纠错' in body['messages'][0]['content'][0]['text']
        return httpx.Response(200,json={'choices':[{'message':{'content':'ABCD01-01 50*80mm [无法辨认]'}}]})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),base_url='https://test.invalid') as client:
            async def get():return client
            monkeypatch.setattr(derivation._client,'get',get)
            stream=io.BytesIO();Image.new('RGB',(100,100)).save(stream,format='PNG')
            result=await media.recognize(stream.getvalue(),'image/png',authorize=lambda:authorized.append(True))
            assert result['text']=='ABCD01-01 50*80mm [无法辨认]'
            def denied():raise ValueError('PERMISSION_OR_SOURCE_CHANGED')
            with pytest.raises(ValueError,match='PERMISSION_OR_SOURCE_CHANGED'):await media.recognize(stream.getvalue(),'image/png',authorize=denied)
    asyncio.run(scenario());assert len(calls)==1


def test_organizer_disables_reasoning_and_declares_strict_output_bounds(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'dashscope_api_key','synthetic')
    sources=[{'key':'body:synthetic','kind':'manual','text':'original'}]
    async def respond(request):
        body=json.loads(request.content)
        assert body['enable_thinking'] is False
        assert '最多12项' in body['messages'][0]['content'] and '不超过40字符' in body['messages'][0]['content']
        assert '界面字段名和示例值不能推成系统自动行为' in body['messages'][0]['content']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'sections':[],'tags':['x'*41]})}}]})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),base_url='https://test.invalid') as client:
            async def get():return client
            monkeypatch.setattr(derivation._client,'get',get)
            value=await derivation.generate(sources,'title',[])
            assert value['_grounding_fallback'] and value['tags']==[]
            assert value['sections'][0]['text']=='original'
            assert value['sections'][0]['grounding_reason']=='invalid_structure'
    asyncio.run(scenario())


def test_model_contract_correction_is_bounded_and_reauthorizes(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'dashscope_api_key','synthetic')
    sources=[{'key':'body:synthetic','kind':'manual','text':'original'}]
    calls=[];checks=[]
    async def respond(request):
        body=json.loads(request.content);calls.append(body)
        if 'checks' in body['messages'][0]['content']:
            value={'checks':[{'index':0,'supported':True,'relevant':True,'evidence':[{'key':'body:synthetic','quote':'original'}]}]}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}]})
        bad=len([b for b in calls if 'checks' not in b['messages'][0]['content']])%2==1
        value={'sections':[{'title':'original','text':'original','source_refs':['wrong' if bad else 'body:synthetic']}],'tags':[]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}]})
    def authorize():checks.append(True)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),base_url='https://test.invalid') as client:
            async def get():return client
            monkeypatch.setattr(derivation._client,'get',get)
            value=await derivation.generate(sources,'title',[],authorize=authorize)
            assert value['sections'][0]['source_refs']==['body:synthetic']
            assert len(calls)==3 and len(checks)==4
            assert 'INVALID_MODEL_SOURCE' in calls[1]['messages'][-1]['content']
            checks.clear()
            def denied_on_retry():
                checks.append(True)
                if len(checks)>1:raise ValueError('PERMISSION_OR_SOURCE_CHANGED')
            with pytest.raises(ValueError,match='PERMISSION_OR_SOURCE_CHANGED'):
                await derivation.generate(sources,'title',[],authorize=denied_on_retry)
            assert len(calls)==4
    asyncio.run(scenario())


def test_video_audio_does_not_extract_an_extra_minute(monkeypatch):
    from pathlib import Path
    commands=[]
    def decode(args,timeout=45):
        commands.append(args)
        if args[0]=='ffprobe':return json.dumps({'format':{'duration':720},'streams':[{'codec_type':'audio'}]}).encode()
        Path(args[-1]).write_bytes(b'synthetic decoder output')
        return b''
    monkeypatch.setattr(media,'run',decode)
    images,audio,partial=media.prepare(b'synthetic video','video/mp4')
    assert len(images)==12 and partial
    assert len(audio)==10 and audio[-1][:2]==(540,600)
    assert all(float(args[args.index('-ss')+1])<600 for args in commands if '-vn' in args)


def test_short_video_samples_span_its_duration(monkeypatch):
    from pathlib import Path
    def decode(args,timeout=45):
        if args[0]=='ffprobe':return json.dumps({'format':{'duration':30},'streams':[]}).encode()
        Path(args[-1]).write_bytes(b'synthetic frame')
        return b''
    monkeypatch.setattr(media,'run',decode)
    images,audio,partial=media.prepare(b'synthetic video','video/mp4')
    assert len(images)==7 and images[0][0]==0 and images[-1][0]>25
    assert not audio and partial


def test_both_query_paths_preserve_source_fact_boundary():
    from services.knowledge_workspace.answer_plan import message
    from services.knowledge_workspace.search_progress import QUERY_INSTRUCTIONS,SOURCE_FACT_INSTRUCTIONS
    assert SOURCE_FACT_INSTRUCTIONS in QUERY_INSTRUCTIONS
    assert SOURCE_FACT_INSTRUCTIONS in message({'mode':'missing','items':[]})['content']
