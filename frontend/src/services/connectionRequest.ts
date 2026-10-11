import { isAxiosError, type AxiosRequestConfig } from 'axios';
import api, { ApiRequestError, toApiRequestError } from './api';

// These routes also return FastAPI HTTPException detail strings. Preserve the
// sanitized service message without exposing validation input or credentials.
export async function connectionRequest<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    return (await api.request<T>(config)).data;
  } catch (error) {
    if (isAxiosError(error) && !error.response?.data?.error) {
      const detail = error.response?.data?.detail;
      if (typeof detail === 'string') {
        throw new ApiRequestError('CONNECTION_REQUEST_FAILED', detail, error.response?.status);
      }
      if (Array.isArray(detail)) {
        throw new ApiRequestError('VALIDATION_ERROR', '连接请求格式不正确，请刷新页面后重试。', error.response?.status);
      }
    }
    throw toApiRequestError(error);
  }
}

