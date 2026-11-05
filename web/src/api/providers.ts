/** C1.6 提供商：test 200 {latency_ms, models} / 4xx {code}；失败红卡注明「密钥未外发」 */
import { api } from './client';
import type { Provider } from './types';

export interface ProviderTestOk {
  latency_ms: number;
  models: string[];
}

export const providersApi = {
  list: () => api.get<Provider[]>('/providers'),
  create: (body: Partial<Provider> & { name: string; base_url: string; api_key?: string }) =>
    api.post<Provider>('/providers', body),
  patch: (id: number, body: Partial<Provider> & { api_key?: string }) =>
    api.patch<Provider>(`/providers/${id}`, body),
  remove: (id: number) => api.del<void>(`/providers/${id}`),
  test: (id: number) => api.post<ProviderTestOk>(`/providers/${id}/test`),
  // 后端包络 data 形状为 { models: [...] }（C0 对象包络），此处解包为数组
  models: (id: number) =>
    api.get<{ models: string[] }>(`/providers/${id}/models`).then((r) => r.models ?? []),
  /** 免保存探测：草稿 base_url/api_key 直连（密钥不落库） */
  probeModels: (body: { protocol?: string; base_url: string; api_key?: string }) =>
    api.post<{ models: string[] }>('/providers/models-probe', body).then((r) => r.models ?? []),
};
