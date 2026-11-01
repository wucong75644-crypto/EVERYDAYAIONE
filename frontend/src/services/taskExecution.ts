import { z } from 'zod';
import api from './api';

const count = z.number().int().nonnegative();
export const taskProgressSchema = z.object({
  root_task_id: z.string().uuid(), revision: count,
  status: z.enum(['active', 'waiting_input', 'waiting_approval', 'waiting_dependencies', 'paused', 'completed', 'partial', 'failed', 'cancelled']),
  completed_steps: count, total_steps: count,
  completed_requirements: count, total_requirements: count,
  pending_results: count, unconfirmed_results: count,
  presentation: z.literal('model_directed').optional(),
  plan_only: z.boolean().optional(),
  image_jobs: z.object({ completed: count, total: count }).optional(),
  working_plan: z.object({
    explanation: z.string().max(2000),
    steps: z.array(z.object({
      step: z.string().min(1).max(300),
      status: z.enum(['pending', 'in_progress', 'completed']),
    })).max(32),
  }).optional(),
  steps: z.array(z.object({
    id: z.string(), title: z.string().max(300),
    status: z.enum(['needs_verification', 'waiting_result', 'completed', 'paused', 'cancelled', 'needs_attention', 'ready', 'waiting_dependency']),
  })).max(32),
});
export type TaskProgress = z.infer<typeof taskProgressSchema>;

export async function getTaskProgress(rootTaskId: string, messageId: string, signal?: AbortSignal): Promise<TaskProgress> {
  const response = await api.get(`/tasks/${rootTaskId}/execution`, { params: { message_id: messageId }, signal });
  const data = taskProgressSchema.parse(response.data);
  if (data.root_task_id !== rootTaskId) throw new Error('TASK_PROGRESS_SOURCE_MISMATCH');
  return data;
}
