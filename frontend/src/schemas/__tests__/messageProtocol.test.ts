import { describe, expect, it, vi } from 'vitest';
import { parseContentPart, parseContentParts } from '../messageProtocol';
import { scheduledTaskForm } from '../../test/fixtures/scheduledTaskForm';

it('keeps product review outcome and reasons after refresh without publishing internal observations', () => {
  const quality = { product_fidelity: { status: 'rejected', issues: ['没有80页的依据'],
    model: 'qwen3.8-flash', version: 'product-fidelity.v1', input_sha256: 'hash',
    observation: { private_user_sources: ['not public'] } } };
  const image = parseContentPart({ type: 'image', url: 'saved.png', failed: true,
    error: '请核验后使用', quality_checks: quality });
  expect(image).toEqual({ type: 'image', url: 'saved.png', failed: true, error: '请核验后使用',
    quality_checks: { product_fidelity: { status: 'rejected', issues: ['没有80页的依据'],
      model: 'qwen3.8-flash', version: 'product-fidelity.v1', input_sha256: 'hash' } } });
  expect(parseContentPart({ type: 'image', url: 'saved.png', quality_checks: {
    product_fidelity: { status: 'passed' } } })).toBeNull();
});

vi.mock('../../utils/logger', () => ({
  logger: { warn: vi.fn() },
}));

describe('messageProtocol', () => {
  it('accepts persisted Skill feedback and strips nonpublic fields', () => {
    expect(parseContentPart({
      type: 'skill_step', step_id: 'manual-skill', status: 'error',
      name: null, revision: null, reason: '当前不可用',
      body: 'secret', nas_path: '/private/path', effective_allowed_tool_names: ['internal'],
    })).toEqual({
      type: 'skill_step', step_id: 'manual-skill', status: 'error', reason: '当前不可用',
    });
  });

  it('accepts a persisted Skill proposal card and normalizes a nullable title', () => {
    expect(parseContentPart({
      type: 'skill_proposal', proposal_id: 'proposal-1', title: '商品图风格拆解', body: 'private',
    })).toEqual({
      type: 'skill_proposal', proposal_id: 'proposal-1', title: '商品图风格拆解',
    });
    expect(parseContentPart({
      type: 'skill_proposal', proposal_id: 'proposal-2', title: null,
    })).toEqual({
      type: 'skill_proposal', proposal_id: 'proposal-2',
    });
  });

  it('preserves the entire scheduled form including date fields and negated visibility', () => {
    expect(parseContentPart(scheduledTaskForm)).toEqual(scheduledTaskForm);
  });

  it('keeps negated visibility even when the date is already known and hidden', () => {
    const form = { ...scheduledTaskForm, fields: scheduledTaskForm.fields.map((field) => (
      field.name === 'run_at' ? { ...field, type: 'hidden' } : field
    )) };
    expect(parseContentPart(form)).toEqual(form);
  });

  it('keeps user images with legacy nullable metadata after refresh', () => {
    const parsed = parseContentPart({
      type: 'image',
      url: 'https://oss.example/input.png',
      original_url: null,
      thumbnail_url: null,
      preview_url: null,
      download_url: null,
      asset_id: null,
      width: null,
      height: null,
      alt: null,
      failed: null,
      error: null,
      name: null,
      workspace_path: null,
      size: null,
      mime_type: null,
    });

    expect(parsed).toEqual({
      type: 'image',
      url: 'https://oss.example/input.png',
    });
  });

  it('accepts every core content block family', () => {
    const blocks = [
      { type: 'text', text: 'hello' },
      { type: 'image', url: null, failed: true },
      { type: 'file', url: '/a', name: 'a.txt', mime_type: 'text/plain' },
      { type: 'thinking', text: 'reasoning' },
      { type: 'tool_result', tool_name: 'erp_agent', text: 'done' },
      { type: 'table', columns: ['name'], rows: [{ name: 'A' }] },
      { type: 'chart', option: { series: [] } },
      { type: 'diagram', format: 'mermaid', source: 'flowchart TD\nA-->B' },
      { type: 'interrupt_marker', interrupted_at: '2026-07-16', reason: 'user_cancel' },
    ];

    expect(parseContentParts(blocks)).toHaveLength(blocks.length);
  });

  it('preserves additive fields on valid blocks', () => {
    expect(parseContentPart({
      type: 'image',
      url: '/image.png',
      workspace_path: 'AI/image.png',
      retry_context: { task: 'retry' },
    })).toMatchObject({
      type: 'image',
      workspace_path: 'AI/image.png',
      retry_context: { task: 'retry' },
    });
  });

  it('rejects malformed known blocks without throwing', () => {
    expect(parseContentPart({ type: 'tool_result', tool_name: 'erp_agent', text: {} }))
      .toBeNull();
  });

  it('serializes object text instead of coercing it to object Object', () => {
    const parsed = parseContentPart({ type: 'text', text: { answer: 42 } });

    expect(parsed).toEqual({ type: 'text', text: '{\n  "answer": 42\n}' });
    expect(parsed && 'text' in parsed ? parsed.text : '').not.toContain('[object Object]');
  });

  it('rejects non-array content collections', () => {
    expect(parseContentParts({ type: 'text', text: 'hello' })).toEqual([]);
  });

  it('rejects invalid diagram formats and empty sources', () => {
    expect(parseContentPart({
      type: 'diagram',
      format: 'plantuml',
      source: '@startuml',
    })).toBeNull();
    expect(parseContentPart({
      type: 'diagram',
      format: 'mermaid',
      source: ' \n ',
    })).toBeNull();
  });

  it('rejects oversized Mermaid source', () => {
    expect(parseContentPart({
      type: 'diagram',
      format: 'mermaid',
      source: 'A'.repeat(100_001),
    })).toBeNull();
  });

  it('preserves unknown historical chart formats as readable fallback data', () => {
    expect(parseContentPart({
      type: 'chart',
      option: { value: 42 },
      spec_format: 'future-engine',
    })).toEqual({
      type: 'chart',
      option: { value: 42 },
      spec_format: 'unknown',
    });
  });
});


it('keeps image task lineage and only accepts real boolean alpha evidence', () => {
  const image={ type: 'image', url: 'saved.png', task_id: 'child', source_task_id: 'old',
    source_message_id: 'message', source_content_index: 3, has_transparency: true,
    quality_checks: { file_integrity: true, alpha_min: 0, alpha_max: 255 } };
  expect(parseContentPart(image)).toEqual(image);
  expect(parseContentPart({ ...image, has_transparency: 'png' })).toBeNull();
  expect(parseContentPart({ type: 'image', url: 'old.png', has_transparency: null, quality_checks: null }))
    .toEqual({ type: 'image', url: 'old.png' });
});

it('retains progress discovery through history and WS parsing without trusting embedded state', () => {
  const root = '11111111-1111-4111-8111-111111111111';
  expect(parseContentPart({ type: 'task_progress', root_task_id: root, status: 'completed', state: { private: 'secret' } }))
    .toEqual({ type: 'task_progress', root_task_id: root });
  expect(parseContentPart({ type: 'task_progress', root_task_id: 'invalid' })).toBeNull();
});


it('preserves delivery failure facts and accepts old nullable metadata', () => {
  expect(parseContentPart({ type: 'text', text: '登记失败', content_incomplete: true })).toMatchObject({
    type: 'text', content_incomplete: true,
  });
  expect(parseContentPart({ type: 'text', text: '旧消息', content_incomplete: null })).toEqual({
    type: 'text', text: '旧消息',
  });
});


it('validates image canvas evidence instead of dropping it or coercing values', () => {
  const evidence = { size_matches: false, actual: { width: 1086, height: 1448, aspect_ratio: '3:4' },
    target: { mode: 'inherit_reference', aspect_ratio: '1:1', resolution: '1K', source: 'reference',
      original_width: 360, original_height: 360, reference_index: 0, reference_sha256: 'a'.repeat(64),
      resolution_source: 'task', source_message_id: 'previous-image', source_task_id: 'previous-task' },
    error: 'IMAGE_SIZE_MISMATCH' };
  const image = { type: 'image', url: 'saved.png', quality_checks: evidence };
  expect(parseContentPart(image)).toEqual(image);
  expect(parseContentPart({ ...image, quality_checks: { ...evidence, size_matches: 'false' } })).toBeNull();
  expect(parseContentPart({ ...image, quality_checks: { ...evidence, actual: { width: -1, height: 1, aspect_ratio: '1:1' } } })).toBeNull();
});

it('preserves native ready plan identities without requiring legacy image prompts', () => {
  const native = { type: 'ecom_plan', plan_id: 'saved-plan', revision: 2, status: 'ready',
    product_insight: 'A5卡册', visual_strategy: '保留实物结构',
    images: [{ item_id: 'first-item', position: 1, name: '封面方案', purpose: '展示封面' }] };
  expect(parseContentPart(native)).toEqual(native);
  expect(parseContentPart({ ...native, plan_id: undefined })).toBeNull();
  expect(parseContentPart({ ...native, revision: undefined })).toBeNull();
  expect(parseContentPart({ type: 'ecom_plan', product_insight: '', visual_strategy: '',
    images: [{ role: '主图', purpose: '展示' }] })).toBeNull();
});

it('does not let a partial native image bypass saved-plan identity checks by adding a prompt', () => {
  expect(parseContentPart({ type: 'ecom_plan', product_insight: '', visual_strategy: '', images: [
    { item_id: 'first', position: 1, name: '封面', purpose: '展示', prompt: '不能代替原生编号' },
  ] })).toBeNull();
});


it('keeps approximate requested pixels separate from matched and actual output dimensions', () => {
  const image = { type: 'image', url: 'approx.png', quality_checks: { size_matches: true,
    target: { mode: 'explicit', aspect_ratio: '16:9', resolution: '2K', approximate: true, requested_width: 1920, requested_height: 1080 },
    actual: { width: 2048, height: 1152, aspect_ratio: '16:9' } } };
  expect(parseContentPart(image)).toEqual(image);
});
