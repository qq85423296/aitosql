import { useState } from 'react';
import { clsx } from 'clsx';
import type { VerifyView } from '../../../api/types';

/**
 * VerifyBlock（FR-Q11）：通过绿块 / 未验证琥珀（照常交付）。
 * 注记：未通过将自动回退重试 1 次，仍不过则照常交付并标注「未验证」（verify_fallback 全局共享 1 次，D09）。
 */
export function VerifyBlock({ verify, unverified }: { verify?: VerifyView; unverified: boolean }) {
  const [open, setOpen] = useState(false);
  if (!verify && !unverified) return null;
  const pass = verify?.passed && !unverified;
  return (
    <div className={clsx('verify-block', pass ? 'pass' : 'warn')}>
      <button className="fold-hd" style={{ padding: 0 }} aria-expanded={open} onClick={() => setOpen(!open)} data-action="折叠展开块">
        <span aria-hidden>{open ? '▾' : '▸'}</span>
        <strong style={{ color: pass ? 'var(--green)' : 'var(--amber)' }}>
          {pass ? '结果校验通过' : '未验证（已照常交付）'}
        </strong>
        {verify?.fallback_used && <span className="mini-badge">已用回退额度</span>}
      </button>
      {(open || !pass) && verify && (
        <div style={{ marginTop: 6 }}>
          {verify.checks.map((c, i) => (
            <div className="item" key={i}>
              <span style={{ color: c.passed ? 'var(--green)' : 'var(--red)' }}>{c.passed ? '✓' : '✗'}</span>
              <span>{c.name}</span>
            </div>
          ))}
          <div className="note">未通过将自动回退重试 1 次，仍不过则照常交付并标注「未验证」</div>
        </div>
      )}
    </div>
  );
}
