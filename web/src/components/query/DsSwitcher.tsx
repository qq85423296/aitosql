import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { DialectBadge } from '../ui/Badge';

/** DsSwitcher（FR-Q2）：数据源切换下拉（原型 .ds-switch：方言徽标 + 名称）；离线库联动 Composer（F7） */
export function DsSwitcher({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const { data: list = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const current = list.find((d) => d.id === value) ?? list[0];

  return (
    <div style={{ position: 'relative' }}>
      <button
        className="ds-switch"
        data-action="切换数据源菜单"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {current ? (
          <>
            <DialectBadge dialect={current.dialect} />
            <span>{current.name}</span>
          </>
        ) : (
          '选择数据源'
        )}
      </button>
      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 20 }} onClick={() => setOpen(false)} />
          <div role="menu" className="ds-switch-menu">
            {list.map((d) => (
              <button
                key={d.id}
                className="btn btn-ghost"
                style={{ width: '100%', justifyContent: 'flex-start' }}
                data-action="选择数据源"
                onClick={() => {
                  onChange(d.id);
                  setOpen(false);
                }}
              >
                <DialectBadge dialect={d.dialect} />
                <span style={{ flex: 1, textAlign: 'left' }}>{d.name}</span>
                {d.mode === 'offline' && <span className="proto-note">仅生成不执行</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
