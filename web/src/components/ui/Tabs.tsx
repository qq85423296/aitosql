import { clsx } from 'clsx';

/** 页签（role=tab，§4.1 无障碍） */
export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { key: string; label: React.ReactNode }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((it) => (
        <button
          key={it.key}
          role="tab"
          aria-selected={value === it.key}
          className={clsx('tab', value === it.key && 'active')}
          onClick={() => onChange(it.key)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
