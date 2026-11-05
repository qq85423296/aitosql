import { clsx } from 'clsx';
import type { ReactNode } from 'react';
import type { Dialect, DsMode, SampleStatus } from '../../api/types';

export function DialectBadge({ dialect, mode }: { dialect: Dialect; mode?: DsMode }) {
  const label = dialect === 'mysql' ? 'MySQL' : dialect === 'doris' ? 'Doris' : 'ClickHouse';
  return (
    <>
      <span className={clsx('badge dialect-badge', dialect)}>{label}</span>
      {mode === 'offline' && <span className="badge offline">离线</span>}
    </>
  );
}

export function StatusChip({ status }: { status: SampleStatus }) {
  const text = status === 'on' ? '启用' : status === 'stale' ? '过期' : '停用';
  return <span className={clsx('badge status-chip', status)}>{text}</span>;
}

export function MiniBadge({ children }: { children: ReactNode }) {
  return <span className="mini-badge">{children}</span>;
}
