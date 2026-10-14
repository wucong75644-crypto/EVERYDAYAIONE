// Immutable analytics API response contracts.
export type Metrics = Record<string, number | null>;
export type Snapshot = {
  id: string;
  metrics: Metrics;
  observed_at: string;
  published_at: string | null;
  coverage: unknown;
  source: string;
  adapter_version: string;
};
export type Published = {
  id: string;
  title: string;
  revision: number;
  account_id: string;
  connection_id: string;
  profile_id: string;
  state: string;
  verification: {
    remote_id?: string;
  };
  latest: Snapshot | null;
  collection: {
    id: string;
    status: string;
    last_error: string;
  } | null;
};
export type Evidence = {
  snapshot_id: string;
  quote: string;
};
export type Conclusion = {
  text: string;
  evidence: Evidence[];
  category?: string;
};
export type Suggestion = {
  id: string;
  kind: string;
  original_text: string;
  decision: string;
  evidence: Evidence[];
  result: unknown;
};
export type Stats = {
  sample_size: number;
  snapshot_count: number;
  coverage: Record<
    string,
    {
      available: number;
      total: number;
    }
  >;
  groups: {
    platform: string;
    content_type: string;
    age_bucket: string;
    sample_size: number;
    insufficient: boolean;
    medians: Metrics;
    scope: string;
  }[];
  contents: {
    snapshot_id: string;
    engagement_rate: number | null;
    formula: string | null;
    rate_missing_reason: string | null;
  }[];
  limitations: string[];
  observed_range: unknown;
};
export type Review = {
  id: string;
  kind: string;
  status: string;
  error: string;
  result: {
    observed_facts: {
      id: string;
      snapshot_id: string;
      metric: string;
      value: number;
    }[];
    possible_causes: Conclusion[];
    comment_insights: Conclusion[];
    insufficient_evidence: string[];
    statistics: Stats;
    comment_coverage: unknown;
  } | null;
  suggestions: Suggestion[];
  input: {
    sources: {
      id: string;
      body: string;
      coverage: unknown;
      source_url: string;
    }[];
    profile_revision_id: string;
    profile_id: string;
    contents: {
      revision: number;
    }[];
  };
  outputs: unknown[];
  usage: unknown;
};
export type Preview = {
  id: string;
  preview_hash: string;
  valid: boolean;
  duplicate_count: number;
  rules: string;
  rows: {
    line: number;
    errors: string[];
    duplicate: boolean;
    data: {
      remote_id: string;
      metrics: Metrics;
    };
  }[];
};
export type Schedule = {
  id: string;
  intent_id: string;
  enabled: boolean;
  next_at: string;
  error: string;
};
