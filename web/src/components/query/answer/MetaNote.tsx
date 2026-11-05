import type { MessageModel } from '../../../api/types';
import { fmtInt } from '../../../lib/format';

/** MetaNote（FR-Q10）：meta 事件两次全字段（post_supply + pre_done）；route 仅 generate/fix 两路 */
export function MetaNote({ msg }: { msg: MessageModel }) {
  if (!msg.meta) return null;
  const parts: string[] = [];
  if (msg.meta.hit_tables?.length) parts.push(`检索命中 ${msg.meta.hit_tables.length} 表（${msg.meta.hit_tables.join('、')}）`);
  if (msg.meta.tokens) parts.push(`tokens ${fmtInt(msg.meta.tokens)}`);
  if (msg.meta.route) parts.push(`路由 generate=${msg.meta.route.generate} · fix=${msg.meta.route.fix}`);
  return <div className="meta-note">{parts.join(' · ')}</div>;
}
