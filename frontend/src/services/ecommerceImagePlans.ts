import api from './api';

export interface EcommercePlanImage {
  item_id: string;
  position: number;
  name: string;
  purpose: string;
  scheme_markdown: string;
  references: Array<Record<string, string | number>>;
  positive_prompt: string;
  negative_prompt: string;
  request_text: string;
  request_text_sha256: string;
  aspect_ratio: string;
}

export interface EcommerceImagePlan {
  id: string;
  revision: number;
  status: string;
  image_count: number;
  target_size?: Record<string, unknown>;
  product_selling_points?: Record<string, unknown>;
  visual_direction?: string;
  images?: EcommercePlanImage[];
  review_records?: Array<Record<string, unknown>>;
  questions?: string[];
}

export async function getEcommerceImagePlan(planId: string, revision: number): Promise<EcommerceImagePlan> {
  const response = await api.get<{ success: boolean; data: EcommerceImagePlan }>(
    `/ecommerce-image-plans/${encodeURIComponent(planId)}`, { params: { revision } },
  );
  return response.data.data;
}
