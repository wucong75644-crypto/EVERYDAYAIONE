import { request } from "./api";
export interface ProfileData {
  name: string;
  positioning: string;
  audience: string;
  facts: string;
  pillars: string[];
  tone: string;
  prohibited: string;
  experience: string;
  visual_template: string;
}
export interface Profile {
  id: string;
  revision_id: string;
  current_revision: number;
  data: ProfileData;
}
export interface ResearchItem {
  id: string;
  generation: number;
  action: string;
  page: number;
  status: string;
  attempts: number;
  error: string;
  result_meta?: { warnings?: string[]; status?: string };
}
export interface Job {
  source_count?: number;
  id: string;
  status: string;
  generation: number;
  connection_id: string;
  last_error: string;
  next_eligible_at: string;
  next_occurrence_at: string | null;
  plan: {
    keywords: string[];
    sample_urls: string[];
    max_items: number;
    comment_pages: number;
    interval_hours: number | null;
  };
  items?: ResearchItem[];
}
export interface Source {
  id: string;
  title: string;
  source_url: string;
  body: string;
  generation: number;
  fetched_at: string;
  coverage: {
    content_kind: string;
    truncated: boolean;
    missing: string[];
    warnings: string[];
    scope: string;
  };
}
export interface Topic {
  title: string;
  audience: string;
  angle: string;
  evidence: { snapshot_id: string; quote: string }[];
  unknowns: string[];
  decision: string;
  planned_date: string | null;
  format: string;
  required_materials: string[];
}
export interface SavedTopic {
  id: string;
  revision: number;
  data: Topic;
}
export interface Draft {
  id: string;
  current_revision: number;
  data: {
    title: string;
    body: string;
    tags: string[];
    evidence_ids?: string[];
    evidence?: { snapshot_id: string; quote: string }[];
    assumptions: string[];
  };
  checks: unknown;
}
export interface Run {
  id: string;
  kind: string;
  status: string;
  stage_index: number;
  outputs: unknown[];
  result: { topics?: Topic[] } | null;
  error: string;
}
export interface SkillOption {
  skill_id: string;
  revision: string;
  name: string;
}
export interface Connection {
  id: string;
  platform: string;
  display_name: string;
  login_state: string;
}
export interface Budget {
  allowed_start_hour: number;
  allowed_end_hour: number;
  max_comment_pages: number;
  daily_limit: number;
  min_interval_seconds: number;
  timezone: string;
  consumed: number;
  breaker: string;
  next_allowed_at: string;
}
export const socialApi = <T>(
  path: string,
  method: "GET" | "POST" | "PATCH" | "PUT" = "GET",
  data?: unknown,
): Promise<T> => request({ url: `/social-ops${path}`, method, data });
export const connections = (): Promise<Connection[]> =>
  request({ url: "/agent-reach/connections", method: "GET" });
export const stageKeys: Record<string, string[]> = {
  plan: [
    "easel-competitor-analysis",
    "easel-content-gap-analysis",
    "easel-topic-evaluator",
    "easel-content-calendar",
  ],
  draft: ["easel-xhs-note-creator"],
  polish: ["easel-text-polisher"],
  check: ["easel-quality-gate", "easel-risk-scanner"],
};
