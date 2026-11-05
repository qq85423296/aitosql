/** 全程本地时区（C0）；{{date}} 注入 YYYY-MM-DD */

export function fmtDate(d: Date | string | number = new Date()): string {
  const dt = d instanceof Date ? d : new Date(d);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}

export function fmtDateTime(d: Date | string | number = new Date()): string {
  const dt = d instanceof Date ? d : new Date(d);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${fmtDate(dt)} ${p(dt.getHours())}:${p(dt.getMinutes())}`;
}

export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function fmtRows(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 10000) return `~${(n / 10000).toFixed(1)} 万行`;
  return `${n.toLocaleString('zh-CN')} 行`;
}

export function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('zh-CN');
}

/** 相对时间（原型「上次摄取 / 最近变更」列）：刚刚 / n 分钟前 / n 小时前 / 昨天 / n 天前 / n 周前 */
export function fmtRel(d: Date | string | number | null | undefined): string {
  if (d == null) return '—';
  const t = (d instanceof Date ? d : new Date(d)).getTime();
  if (Number.isNaN(t)) return '—';
  const diff = Date.now() - t;
  if (diff < 60_000) return '刚刚';
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 24 * 3600_000) return `${Math.floor(diff / 3600_000)} 小时前`;
  if (diff < 48 * 3600_000) return '昨天';
  const days = Math.floor(diff / (24 * 3600_000));
  if (days < 7) return `${days} 天前`;
  return `${Math.floor(days / 7)} 周前`;
}

/**
 * 路由引用（contracts.md C7 冻结）：值格式 `{provider_id}:{model}`——id 稳定不受重命名影响（P0-3）。
 * 解析 provider_id；非法/空引用返回 null。
 */
export function parseRouteRef(ref: string | null | undefined): { providerId: number; model: string } | null {
  if (!ref) return null;
  const idx = ref.indexOf(':');
  if (idx < 0) return null;
  const providerId = Number(ref.slice(0, idx));
  if (!Number.isInteger(providerId)) return null;
  return { providerId, model: ref.slice(idx + 1) };
}

/** 路由引用展示：解析为「提供商名 / 模型」，未匹配时原样回显 */
export function fmtRouteRef(ref: string | null | undefined, providers: { id: number; name: string }[]): string {
  const r = parseRouteRef(ref);
  if (!r) return ref || '…';
  const p = providers.find((x) => x.id === r.providerId);
  return p ? `${p.name} / ${r.model}` : (ref ?? '…');
}

/** 注释中提取中文段（双语标签用）：去 ASCII 词/空白/边缘标点，超长截断；取不到返回空串 */
export function cnName(comment: string | null | undefined): string {
  if (!comment) return '';
  const s = comment
    .replace(/[A-Za-z0-9_]+/g, '')
    .replace(/\s+/g, '')
    .replace(/^[-—·:：/|]+|[-—·:：/|]+$/g, '');
  return s.length > 16 ? s.slice(0, 16) + '…' : s;
}
