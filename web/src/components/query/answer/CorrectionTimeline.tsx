import { useState } from 'react';
import { clsx } from 'clsx';
import type { CorrectionRoundView } from '../../../api/types';

/** CorrectionTimeline（FR-Q11）：修正回路时间线（correction 事件逐轮；错误已脱敏回显，C9.3） */
export function CorrectionTimeline({ rounds }: { rounds: CorrectionRoundView[] }) {
  const [open, setOpen] = useState(false);
  if (!rounds.length) return null;
  return (
    <div className="fold-block">
      <button className="fold-hd" aria-expanded={open} onClick={() => setOpen(!open)} data-action="展开修正时间线">
        <span aria-hidden>{open ? '▾' : '▸'}</span>
        <span>修正回路 · {rounds.length} 轮{rounds[rounds.length - 1]?.error === '成功' ? '收敛' : ''}</span>
      </button>
      {open && (
        <div className="fold-bd">
          <div className="timeline">
            {rounds.map((r) => (
              <div key={r.round} className={clsx('tl-item', r.error === '成功' ? 'ok' : 'fail')}>
                <div>
                  <strong>第 {r.round} 轮</strong> · {r.error}
                </div>
                <div className="proto-note" style={{ fontFamily: 'var(--mono)' }}>
                  fingerprint: {r.fingerprint}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
