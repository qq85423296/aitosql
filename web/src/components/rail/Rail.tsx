import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';
import { useTheme } from '../../hooks/useTheme';
import { Icon, type IconName } from '../ui/icons';

/** Rail 64px（原型）：AI logo + 四页导航（SVG 图标 + 9px 标签 + 激活态左侧指示条）+ 主题切换 */
export function Rail() {
  const { theme, toggleTheme } = useTheme();
  const items: { to: string; icon: IconName; label: string; end?: boolean }[] = [
    { to: '/', icon: 'chat', label: '查询', end: true },
    { to: '/datasources', icon: 'db', label: '数据源' },
    { to: '/samples', icon: 'book', label: '样例库' },
    { to: '/settings', icon: 'gear', label: '设置' },
  ];
  return (
    <nav className="rail" aria-label="主导航">
      <div className="logo" aria-hidden>
        AI
      </div>
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          end={it.end}
          className={({ isActive }) => clsx('rail-item', isActive && 'active')}
        >
          <Icon name={it.icon} size={20} strokeWidth={1.5} />
          <span className="lbl">{it.label}</span>
        </NavLink>
      ))}
      <div className="spacer" />
      <button
        className="rail-item"
        onClick={toggleTheme}
        aria-label="切换主题"
        title={theme === 'dark' ? '切换浅色' : '切换深色'}
      >
        <span className="ico" aria-hidden>
          ◐
        </span>
        <span className="lbl">主题</span>
      </button>
    </nav>
  );
}
