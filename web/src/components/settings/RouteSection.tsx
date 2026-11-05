import { useQuery } from '@tanstack/react-query';
import { providersApi } from '../../api/providers';
import { EGRESS_ITEMS, EGRESS_NEVER } from '../../lib/egress';
import type { SettingsMap } from '../../api/types';

/**
 * RouteSection（FR-C3 / F11 / D07）：三路路由（generate/fix/ai）+ 出境清单矩阵联动。
 * 任一路由含云端 → 展示 E1-E8（E6 红标仅 verify_llm 开启时）+ verify_llm 解锁；
 * 三路全本地 = local_only 零出境 → 清单隐藏 + verify_llm 置灰禁用。
 */
export function RouteSection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (patch: Partial<SettingsMap>) => void;
}) {
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: providersApi.list });

  // 路由值格式（contracts.md C7 冻结）：`{provider_id}:{model}`——id 稳定不受重命名影响（P0-3）
  const options = providers.flatMap((p) =>
    p.models.map((m) => ({ value: `${p.id}:${m}`, label: `${p.name} / ${m}`, cloud: p.type !== 'local' })),
  );

  const routes = {
    generate: String(settings['route.generate'] ?? ''),
    fix: String(settings['route.fix'] ?? ''),
    ai: String(settings['route.ai'] ?? ''),
  };
  const hasCloud = [routes.generate, routes.fix, routes.ai].some((r) =>
    options.some((o) => o.value === r && o.cloud),
  );
  const verifyLlm = !!settings['feature.verify_llm'];

  return (
    <div>
      {(['generate', 'fix', 'ai'] as const).map((key) => (
        <div className="setting-row" key={key}>
          <div className="lab">
            <div className="name">route.{key}</div>
            <div className="hint">
              {key === 'generate' ? 'SQL 生成主路由' : key === 'fix' ? '修正回路路由' : 'AI 补全/记忆合并路由'}
            </div>
          </div>
          <div className="ctl">
            <select
              className="form-select"
              data-action="路由变更"
              value={routes[key]}
              onChange={(e) => onChange({ [`route.${key}`]: e.target.value })}
            >
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                  {o.cloud ? '（云）' : '（本地）'}
                </option>
              ))}
              {/* 已保存值不在当前选项（提供商被删/旧格式）时兜底回显，避免下拉静默漂移 */}
              {(options.length === 0 || !options.some((o) => o.value === routes[key])) && routes[key] && (
                <option value={routes[key]}>{routes[key]}（未匹配）</option>
              )}
            </select>
          </div>
        </div>
      ))}

      {hasCloud ? (
        <div style={{ marginTop: 12 }}>
          <div className="banner banner-amber" style={{ maxWidth: 'none', margin: '0 0 10px' }}>
            当前含云端路由：以下内容将出境
          </div>
          <table className="dt egress-table">
            <thead>
              <tr><th style={{ width: 60 }}>类目</th><th>名称</th><th>内容</th></tr>
            </thead>
            <tbody>
              {EGRESS_ITEMS.map((e) =>
                e.onlyWhenVerifyLlm && !verifyLlm ? null : (
                  <tr key={e.id}>
                    <td className={e.red ? 'e6' : undefined} style={{ fontFamily: 'var(--mono)' }}>{e.id}</td>
                    <td className={e.red ? 'e6' : undefined}>{e.name}</td>
                    <td className="proto-note">{e.content}</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
          <div className="proto-note" style={{ marginTop: 8 }}>
            绝不外发：{EGRESS_NEVER.join('、')}
          </div>
        </div>
      ) : (
        <div className="banner banner-green" style={{ maxWidth: 'none', margin: '12px 0 0' }}>
          三路全本地 = local_only 零出境；verify_llm 置灰禁用（D07）
        </div>
      )}
    </div>
  );
}
