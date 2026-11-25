"""Replay actual Image 2 wire data with uploaded, quoted and generated identities."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.manual_skill_formal_entry import assert_image2_transport


def capture():
    return json.loads(Path(__file__).with_name('fixtures').joinpath(
        'chat_skill_image2_delivery_20261123.json').read_text())['transport']


def test_full_actual_image2_transport_preserves_prompts_order_and_polished_edit():
    value = capture()
    assert all(case['status'] == 'completed' for case in value['cases'])
    assert_image2_transport(value)


@pytest.mark.parametrize('defect', ['wrong_model', 'rewritten_prompt', 'swapped_images',
    'internal_identity', 'missing_identity', 'discarded_polishing'])
def test_actual_capture_rejects_transport_or_identity_regressions(defect):
    value = deepcopy(capture())
    case = value['cases'][0]
    post = value['wire'][0]
    if defect == 'wrong_model':
        post['body']['model'] = 'gpt-image-2-5-sunburst-image-to-image'
    elif defect == 'rewritten_prompt':
        post['body']['input']['prompt'] += '\n后台额外规则'
    elif defect == 'swapped_images':
        post['body']['input']['input_urls'].reverse()
    elif defect == 'internal_identity':
        identity = case['snapshot']['references'][0]['file_id']
        case['snapshot']['prompt'] += '\n' + identity
        post['body']['input']['prompt'] = case['snapshot']['prompt']
    elif defect == 'missing_identity':
        case['snapshot']['references'][0] = {'workspace_path':case['snapshot']['references'][0]['workspace_path']}
    else:
        value['cases'][-1]['snapshot']['prompt'] = '参考图一的波点，给图二加一些波点。'
        value['wire'][4]['body']['input']['prompt'] = value['cases'][-1]['snapshot']['prompt']
    with pytest.raises(AssertionError):
        assert_image2_transport(value)
