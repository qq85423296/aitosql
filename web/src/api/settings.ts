/** C1.6 设置 + C4.2 组装预览 */
import { api } from './client';
import type { PromptPreview, SettingsMap } from './types';

/**
 * 设置读写包络（contracts.md C1.6 冻结，P1-3 修复）：
 * - GET /settings 响应 `{settings: {key: {value, desc}}, local_only, verify_llm_greyed}`
 * - PUT /settings 请求 `{values: {key: value}}`（未知键/类型不符 422）
 * 本模块为唯一适配点：对消费面暴露平铺 SettingsMap + 元信息，组件不感知后端包络。
 */
export interface SettingsBundle {
  /** 平铺键值（40+ 键，C7） */
  settings: SettingsMap;
  /** key → 说明文案（SETTING_DEFAULTS 回显） */
  descs: Record<string, string>;
  /** 三路全本地 = 零出境（D07） */
  local_only: boolean;
  /** local_only 下 verify_llm 置灰禁用 */
  verify_llm_greyed: boolean;
}

interface RawSettingsResponse {
  settings: Record<string, { value: unknown; desc?: string }>;
  local_only: boolean;
  verify_llm_greyed: boolean;
}

export const settingsApi = {
  get: async (): Promise<SettingsBundle> => {
    const raw = await api.get<RawSettingsResponse>('/settings');
    const flat: Record<string, unknown> = {};
    const descs: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw.settings ?? {})) {
      flat[k] = v?.value;
      if (v?.desc) descs[k] = v.desc;
    }
    return {
      settings: flat as SettingsMap,
      descs,
      local_only: !!raw.local_only,
      verify_llm_greyed: !!raw.verify_llm_greyed,
    };
  },
  put: (body: Partial<SettingsMap>) => api.put<void>('/settings', { values: body }),
  /** 8 段信封预览（C4.2 逐字段）；datasource_id 必填（P1-7），缺省由调用方取当前/第一可用数据源 */
  promptPreview: (datasourceId: number) =>
    api.get<PromptPreview>(`/settings/prompt-preview?datasource_id=${datasourceId}`),
};
