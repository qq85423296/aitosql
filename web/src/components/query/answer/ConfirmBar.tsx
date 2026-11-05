import type { RiskItem } from '../../../api/types';
import { fmtInt } from '../../../lib/format';
import { Button } from '../../ui/Button';

/**
 * ConfirmBar（F2/FR-Q6 写确认门）：风险清单（R06 红/R03 黄）+ est_rows → 确认/取消两终态。
 * 门裁决走 POST /queries/:id/resume（CAS，仅 waiting_confirm 受理）；optimistic 回显。
 */
export function ConfirmBar({
  risks,
  estRows,
  decision,
  onDecide,
}: {
  risks: RiskItem[];
  estRows?: number;
  /** 已裁决（optimistic 或服务端回显）：approved=true 已执行 / false 已取消 */
  decision?: { approved: boolean } | null;
  onDecide?: (approved: boolean) => void;
}) {
  if (decision) {
    return (
      <div className="confirm-bar" role="status">
        <span style={{ color: decision.approved ? 'var(--green)' : 'var(--t2)' }}>
          {decision.approved
            ? `已确认 · 已执行${estRows != null ? `（影响行数 ${fmtInt(estRows)}）` : ''}`
            : '已取消，语句未执行'}
        </span>
      </div>
    );
  }
  return (
    <div className="confirm-bar">
      <strong style={{ color: 'var(--amber)' }}>写操作确认门</strong>
      <div className="risk-list">
        {risks.map((r, i) => (
          <div key={i} className={`risk-item ${r.level}`}>
            <span className="rule">{r.rule_id}</span>
            <span>{r.message}</span>
          </div>
        ))}
        {estRows != null && <div className="risk-item">预计影响行数：{fmtInt(estRows)}</div>}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Button variant="amber" size="sm" data-action="确认写操作" onClick={() => onDecide?.(true)}>
          确认执行
        </Button>
        <Button variant="ghost" size="sm" data-action="取消写操作" onClick={() => onDecide?.(false)}>
          取消
        </Button>
      </div>
    </div>
  );
}
