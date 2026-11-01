"""Each source-backed delivery obligation retains its own quantity and purpose."""
from types import SimpleNamespace

import pytest

from services.task_execution.bootstrap import StartTaskInput
from services.task_execution.requirement_extraction import RequirementObservation,compare_observation


def compare(goal,observed,proposed):
    evidence=SimpleNamespace(original_goal=goal)
    observation=RequirementObservation(action='plan',deliveries=tuple(observed))
    proposal=StartTaskInput(action='plan',requirements=tuple({'id':str(i),'description':d['evidence'][0],**d}
        for i,d in enumerate(proposed)))
    return compare_observation(evidence,proposal,observation)


def item(quote,kind='text',minimum=1,item_count=None):
    return dict(artifact_kind=kind,minimum=minimum,item_count=item_count,evidence=(quote,))


@pytest.mark.parametrize('count',[None,1,4,5])
def test_analysis_and_prompt_collection_preserve_each_text_obligation(count):
    goal='先做商品分析，再给五条完整提示词'
    expected=[item('商品分析'),item('五条完整提示词',item_count=5)]
    proposed=[item('商品分析'),item('五条完整提示词',item_count=count)]
    assert compare(goal,expected,proposed).coverage==('covered' if count==5 else 'missing')


def test_two_main_and_three_detail_images_cannot_be_replaced_by_five_main_images():
    goal='生成两张主图和三张详情图'
    expected=[item('两张主图','image',2),item('三张详情图','image',3)]
    assert compare(goal,expected,[item('两张主图','image',5)]).coverage=='missing'
    assert compare(goal,expected,expected).coverage=='covered'
    assert compare(goal,expected,[item(goal,'image',5)]).coverage=='uncertain'


def test_counted_collection_cannot_lose_its_metric_when_frozen_as_separate_requirements():
    goal='先做商品分析，再给五条完整提示词'
    expected=[item('商品分析'),item('五条完整提示词',item_count=5)]
    proposed=[item('商品分析'),*[item('五条完整提示词') for _ in range(5)]]
    assert compare(goal,expected,proposed).coverage=='uncertain'


def test_broad_text_quotes_cannot_hide_missing_prompt_items_in_analysis():
    goal='先做商品分析，再给五条完整提示词'
    expected=[item('商品分析'),item('五条完整提示词',item_count=5)]
    assert compare(goal,expected,[item(goal),item(goal,item_count=5)]).coverage=='uncertain'


def test_text_obligations_are_not_reported_as_mandatory_separate_documents():
    goal='先做商品分析，再给五条完整提示词'
    expected=[item('商品分析'),item('五条完整提示词',item_count=5)]
    result=compare(goal,expected,[item('五条完整提示词',item_count=5)])
    assert result.coverage=='missing'
    assert any('内容要求' in issue and '同一份完整文本' in issue for issue in result.issues)
    assert compare(goal,expected,expected).coverage=='covered'


@pytest.mark.parametrize('reverse',[False,True])
def test_shared_restriction_quote_resolves_only_with_distinct_source_evidence(reverse):
    shared='本次仅完成文字策划和完整提示词，不生成新图片'
    goal='帮我生成五张详情页的图；'+shared
    observed=[item(shared,item_count=1),dict(item('帮我生成五张详情页的图',item_count=5),evidence=('帮我生成五张详情页的图',shared))]
    proposed=[item(shared,item_count=1),dict(item(shared,item_count=5),evidence=(shared,'帮我生成五张详情页的图'))]
    if reverse:proposed.reverse();observed.reverse()
    assert compare(goal,observed,proposed).coverage=='covered'
    # The same shared quote and quantities alone cannot invent a purpose.
    assert compare(goal,observed,[item(shared,item_count=1),item(shared,item_count=5)]).coverage=='uncertain'
    proposed[0]['item_count']=7
    assert compare(goal,observed,proposed).coverage=='missing'
