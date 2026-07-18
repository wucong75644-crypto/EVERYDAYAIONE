import { describe, expect, it, vi } from 'vitest';
import { parseContentPart, parseContentParts } from '../messageProtocol';

vi.mock('../../utils/logger', () => ({
  logger: { warn: vi.fn() },
}));

describe('messageProtocol', () => {
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
