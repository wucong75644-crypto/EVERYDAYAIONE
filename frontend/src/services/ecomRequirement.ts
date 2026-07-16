import { request } from './api';
import type { DetailGenerationForm } from '../types/detailPage';
import type {
  RequirementSuggestionsEnvelope,
  RequirementSuggestionsRequest,
} from '../types/ecomRequirement';


export function buildRequirementSuggestionsRequest(
  projectId: string,
  form: DetailGenerationForm,
): RequirementSuggestionsRequest {
  return {
    source: { type: 'detail_project', project_id: projectId },
    settings: {
      content_type: form.contentType,
      platform: form.platform,
      language: form.language,
      aspect_ratio: form.aspectRatio,
      quality: form.quality,
      image_count: form.count,
      requirement: form.requirement,
    },
  };
}


export async function generateRequirementSuggestions(
  projectId: string,
  form: DetailGenerationForm,
  signal?: AbortSignal,
): Promise<RequirementSuggestionsEnvelope> {
  return request<RequirementSuggestionsEnvelope>({
    method: 'POST',
    url: '/ecom-image/requirement-suggestions',
    data: buildRequirementSuggestionsRequest(projectId, form),
    signal,
    timeout: 105_000,
  });
}
