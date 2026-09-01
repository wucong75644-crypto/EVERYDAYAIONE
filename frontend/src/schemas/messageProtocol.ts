/** Runtime protocol boundary for message content received from external sources. */

import { z } from 'zod';
import type { ContentPart } from '../types/message';
import { logger } from '../utils/logger';

const optionalString = z.string().optional();
const optionalNumber = z.number().optional();

// Older API responses were serialized with Pydantic's default model_dump(),
// which emitted null for every optional media field. Treat those nulls as the
// equivalent of an omitted field so historical user attachments remain
// renderable after a refresh.
const nullEquivalentFields: Record<string, readonly string[]> = {
  image: [
    'original_url', 'thumbnail_url', 'preview_url', 'download_url', 'asset_id',
    'width', 'height', 'alt', 'failed', 'error', 'error_code', 'name',
    'workspace_path', 'size', 'mime_type',
  ],
  video: ['duration', 'thumbnail'],
  audio: ['duration', 'transcript'],
  file: ['size', 'workspace_path', 'asset_id'],
  thinking: ['duration_ms'],
  tool_step: [
    'summary', 'code', 'output', 'elapsed_ms', 'input', 'cancelled_at',
  ],
  tool_result: ['files'],
  form: ['title', 'description', 'submit_text', 'cancel_text', 'change_set_id'],
  changeset: ['title', 'resource_type', 'snapshot'],
  chart: ['title', 'chart_type', 'spec_format'],
  diagram: ['title'],
  table: ['title', 'truncated'],
  ecom_plan: ['cost_estimate'],
};

function normalizeNullEquivalentFields(input: unknown): unknown {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return input;
  const candidate = input as Record<string, unknown>;
  const fields = nullEquivalentFields[String(candidate.type)] ?? [];
  if (!fields.some((field) => candidate[field] === null)) return input;
  const normalized = { ...candidate };
  fields.forEach((field) => {
    if (normalized[field] === null) delete normalized[field];
  });
  return normalized;
}

function normalizeChartFormat(
  value: string,
): 'echarts' | 'plotly' | 'vegalite' | 'unknown' {
  if (value === 'echarts' || value === 'plotly' || value === 'vegalite') {
    return value;
  }
  return 'unknown';
}

const fileRefSchema = z.object({
  url: z.string(),
  name: z.string(),
  mime_type: z.string(),
  size: optionalNumber,
}).passthrough();

const formFieldSchema = z.object({
  type: z.enum(['text', 'textarea', 'select', 'checkbox_group', 'number', 'time', 'hidden']),
  name: z.string(),
  label: z.string(),
  required: z.boolean().optional(),
  default_value: z.union([
    z.string(),
    z.number(),
    z.array(z.number()),
    z.boolean(),
  ]).optional(),
  placeholder: optionalString,
  options: z.array(z.object({ label: z.string(), value: z.string() })).optional(),
  visible_when: z.object({ field: z.string(), value: z.string() }).optional(),
}).passthrough();

const contentPartSchema = z.preprocess(normalizeNullEquivalentFields, z.discriminatedUnion('type', [
  z.object({ type: z.literal('text'), text: z.string() }).passthrough(),
  z.object({
    type: z.literal('image'),
    url: z.string().nullable(),
    original_url: optionalString,
    thumbnail_url: optionalString,
    preview_url: optionalString,
    download_url: optionalString,
    asset_id: optionalString,
    width: optionalNumber,
    height: optionalNumber,
    alt: optionalString,
    failed: z.boolean().optional(),
    error: optionalString,
    error_code: optionalString,
    name: optionalString,
    workspace_path: optionalString,
    size: optionalNumber,
    mime_type: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('video'),
    url: z.string(),
    duration: optionalNumber,
    thumbnail: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('audio'),
    url: z.string(),
    duration: optionalNumber,
    transcript: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('file'),
    url: z.string(),
    name: z.string(),
    mime_type: z.string(),
    size: optionalNumber,
    workspace_path: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('thinking'),
    text: z.string(),
    duration_ms: optionalNumber,
  }).passthrough(),
  z.object({
    type: z.literal('tool_step'),
    tool_name: z.string(),
    tool_call_id: z.string(),
    status: z.enum(['running', 'completed', 'error', 'cancelled']),
    summary: optionalString,
    code: optionalString,
    output: optionalString,
    elapsed_ms: optionalNumber,
    input: optionalString,
    cancelled_at: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('tool_result'),
    tool_name: z.string(),
    text: z.string(),
    files: z.array(fileRefSchema).optional(),
  }).passthrough(),
  z.object({
    type: z.literal('form'),
    form_type: z.string(),
    form_id: z.string(),
    title: optionalString,
    description: optionalString,
    fields: z.array(formFieldSchema),
    submit_text: optionalString,
    cancel_text: optionalString,
    status: z.enum(['open', 'submitting', 'cancelled', 'submitted']).optional(),
    result_message: optionalString,
    error_message: optionalString,
    change_set_id: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('changeset'),
    change_set_id: z.string(),
    title: optionalString,
    resource_type: optionalString,
    snapshot: z.record(z.string(), z.unknown()).optional(),
  }).passthrough(),
  z.object({
    type: z.literal('chart'),
    option: z.record(z.string(), z.unknown()),
    title: optionalString,
    chart_type: optionalString,
    spec_format: z.string().transform(normalizeChartFormat).optional(),
  }).passthrough(),
  z.object({
    type: z.literal('diagram'),
    format: z.literal('mermaid'),
    source: z.string().min(1).max(100_000).refine((value) => value.trim().length > 0),
    title: optionalString,
  }).passthrough(),
  z.object({
    type: z.literal('table'),
    title: optionalString,
    columns: z.array(z.string()),
    rows: z.array(z.record(z.string(), z.unknown())),
    truncated: z.boolean().optional(),
  }).passthrough(),
  z.object({
    type: z.literal('ecom_plan'),
    product_insight: z.string(),
    visual_strategy: z.string(),
    images: z.array(z.object({
      role: z.string(),
      purpose: z.string(),
      title: z.string(),
      subtitle: z.string(),
      prompt: z.string(),
      aspect_ratio: z.string(),
      has_text: z.boolean(),
      image_type: z.string(),
    }).passthrough()),
    cost_estimate: z.object({
      estimated_credits: z.number(),
      image_count: z.number(),
    }).optional(),
  }).passthrough(),
  z.object({
    type: z.literal('interrupt_marker'),
    interrupted_at: z.string(),
    reason: z.enum(['user_cancel', 'user_pause', 'system_timeout', 'network_error']),
  }).passthrough(),
]));

export interface MessageProtocolContext {
  messageId?: string;
  conversationId?: string;
  source?: string;
}

export function parseProtocolString(
  input: unknown,
  field: string,
  context: MessageProtocolContext = {},
): string | null {
  if (typeof input === 'string') return input;
  if (input !== undefined && input !== null) {
    logger.warn('message:protocol', 'Non-string protocol field rejected', {
      ...context,
      field,
      receivedType: Array.isArray(input) ? 'array' : typeof input,
    });
  }
  return null;
}

function stringifyStructuredText(value: unknown): string | null {
  try {
    const serialized = JSON.stringify(value, null, 2);
    return serialized ?? null;
  } catch {
    return null;
  }
}

function recoverStructuredText(input: unknown): ContentPart | null {
  if (!input || typeof input !== 'object') return null;
  const candidate = input as Record<string, unknown>;
  if (candidate.type !== 'text' || typeof candidate.text === 'string') return null;
  const text = stringifyStructuredText(candidate.text);
  return text === null ? null : { ...candidate, type: 'text', text } as ContentPart;
}

export function parseContentPart(
  input: unknown,
  context: MessageProtocolContext = {},
): ContentPart | null {
  const result = contentPartSchema.safeParse(input);
  if (result.success) return result.data as ContentPart;

  const recovered = recoverStructuredText(input);
  logger.warn('message:protocol', recovered
    ? 'Structured value recovered as JSON text'
    : 'Invalid content block rejected', {
    ...context,
    blockType: input && typeof input === 'object' && 'type' in input
      ? String(input.type)
      : typeof input,
  });
  return recovered;
}

export function parseContentParts(
  input: unknown,
  context: MessageProtocolContext = {},
): ContentPart[] {
  if (!Array.isArray(input)) {
    logger.warn('message:protocol', 'Content array rejected', {
      ...context,
      receivedType: typeof input,
    });
    return [];
  }

  return input.flatMap((part) => {
    const parsed = parseContentPart(part, context);
    return parsed ? [parsed] : [];
  });
}
