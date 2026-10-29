"""Provenance, rather than summary wording, controls rollup growth."""
from services.knowledge_workspace.originals import bound_sources,child_records,merge,origin
from services.knowledge_workspace.chunking import source_chunks
from services.knowledge_workspace.derivation import extractive_fallback
from services.knowledge_workspace.derived_document import effective_document


def rollup(child_id,sources,body=''):
    chunks=source_chunks(sources)
    # These quotations intentionally repeat subsets of the actual originals.
    doc={'chunks':chunks,'sections':[{'id':'s','kind':'material','title':'AI','text':'不可当作原文的解释',
        'source_refs':[s['key'] for s in sources],'grounding_version':'grounding-v1',
        'evidence':[{'key':sources[0]['key'],'quote':sources[0]['text'][:5],'kind':'original'}]}]}
    originals=child_records({'id':child_id,'revision':1,'body':body},effective_document(doc))
    text='\n\n'.join(r['text'] for r in originals)
    return {'key':'child:'+child_id,'title':'分支','kind':'child','text':text,'grounding_text':text,'_originals':originals}


def test_fifty_levels_keep_exact_original_once_without_quotes_or_generated_prose():
    text='条件：\r\n'+'金额须保持1.25元，例外先核对。'*180+'\n\n结束'
    source={'key':'file:f','title':'原始附件','kind':'file','text':text,'dependency':{'version_id':'v1'}}
    for i in range(50):source=rollup(str(i),[source])
    assert source['text']==text
    assert len(source['_originals'])==1
    assert len(source_chunks([source]))<20


def test_same_words_from_different_versions_and_sources_do_not_collapse():
    text='原文内容'
    sources=[{'key':'file:'+key,'title':key,'kind':'file','text':text,'dependency':{'version_id':version}}
             for key,version in [('a','v1'),('b','v1'),('a','v2')]]
    rolled=rollup('root',sources)
    assert len(rolled['_originals'])==3 and rolled['text'].count(text)==3


def test_overlapping_and_contained_spans_merge_by_exact_identity():
    assert merge([{'origin':'a','start':0,'text':'abcdefgh'}, {'origin':'a','start':2,'text':'cd'},
                  {'origin':'a','start':6,'text':'ghijk'}])==[{'origin':'a','start':0,'text':'abcdefghijk'}]


def test_bounds_apply_to_originals_and_include_separator_without_unbounded_side_channel():
    records=[{'origin':origin(str(i)),'start':0,'text':'x'*10} for i in range(3)]
    result,partial=bound_sources([{'key':'child:x','title':'x','kind':'child','text':'short',
                                 'grounding_text':'x'*1000,'_originals':records}],17)
    assert partial and len(result[0]['text'])==17
    assert result[0]['text']==result[0]['grounding_text']
    assert len(result[0]['_originals'])==2


def test_directory_fallback_is_branch_navigation_not_recursive_original_dump():
    sources=[{'key':'child:a','title':'采购','kind':'child','text':'原始细节'*10000,'grounding_text':'原始细节'*10000}]
    value=extractive_fallback(sources);section=value['sections'][0]
    assert section['text']=='采购' and section['grounding_mode']=='navigation_fallback'
    doc=effective_document({'sections':[{**section,'id':'s','kind':'rollup'}],'chunks':source_chunks(sources)})
    assert doc['sections'][0]['verification']=='navigation'
    assert all(c['text']!='采购' for c in doc['chunks'] if c['kind']=='source')


def test_conflicting_spans_of_one_immutable_source_fail_instead_of_creating_two_truths():
    import pytest
    with pytest.raises(ValueError,match='ORIGINAL_SPAN_CONFLICT'):
        merge([{'origin':'same-version','start':0,'text':'金额100'},
               {'origin':'same-version','start':2,'text':'999'}])


def test_manual_correction_uses_same_origin_from_source_and_effective_section():
    from services.knowledge_workspace.originals import manual_record
    original=manual_record('child','section:manual','人工更正后的条件')
    chunks=source_chunks([{'key':'override:section:manual','title':'更正','kind':'manual',
        'text':original['text'],'_originals':[original]}])
    effective={'chunks':chunks,'sections':[{'id':'section:manual','manual':True,'text':original['text']}]}
    combined=child_records({'id':'child','revision':1,'body':''},effective)
    assert combined==[original]


def test_unreadable_child_retains_authorized_navigation_without_fabricated_original_text():
    source={'key':'child:empty','kind':'child','title':'尚未解析的视频','text':'','grounding_text':'','_originals':[]}
    bounded,_=bound_sources([source],100)
    assert bounded and not source_chunks(bounded)
    section=extractive_fallback(bounded)['sections'][0]
    doc=effective_document({'sections':[{**section,'id':'s','kind':'rollup'}],
                            'dependencies':[{'object_id':'empty','revision':1}],'chunks':[]})
    assert doc['sections'][0]['verification']=='navigation'
    assert doc['sections'][0]['text']=='尚未解析的视频'
    assert not any(c['kind']=='source' for c in doc['chunks'])
