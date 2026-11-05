import { clsx } from 'clsx';
import type { Assumption } from '../../../api/types';

/** AssumptionChips：假设芯片（FR-Q5）；impact=low 蓝 / high 琥珀「推测」；修正 → F4 Composer 预填 */
export function AssumptionChips({
  assumptions,
  onFix,
}: {
  assumptions: Assumption[];
  onFix?: (a: Assumption) => void;
}) {
  if (!assumptions.length) return null;
  return (
    <div className="assumption-chips">
      {assumptions.map((a, i) => (
        <span key={i} className={clsx('assump-chip', a.impact === 'high' && 'high')}>
          {a.impact === 'high' && <span aria-hidden>推测 · </span>}
          {a.text}
          {onFix && (
            <button
              className="fix"
              data-action="修正假设"
              onClick={() => onFix(a)}
              title="修正后直写 memory_log（D-14）"
            >
              修正
            </button>
          )}
        </span>
      ))}
    </div>
  );
}
