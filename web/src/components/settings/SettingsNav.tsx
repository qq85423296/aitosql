import { clsx } from 'clsx';

/** 设置分组导航 180px（点击平滑滚动；内容区滚动联动高亮由 SettingsPage 承载） */
export const SETTING_GROUPS = [
  { id: 'g-providers', label: '提供商' },
  { id: 'g-route', label: '路由与出境' },
  { id: 'g-aicompletion', label: 'AI 补全' },
  { id: 'g-prompt', label: '提示词' },
  { id: 'g-memory', label: '记忆' },
  { id: 'g-general', label: '通用' },
  { id: 'g-safety', label: '查询安全' },
  { id: 'g-schema', label: 'Schema 注入' },
  { id: 'g-sample', label: '样例缓存' },
  { id: 'g-backup', label: '备份' },
  { id: 'g-about', label: '关于' },
] as const;

export function SettingsNav({ active }: { active: string }) {
  return (
    <nav className="settings-nav" aria-label="设置分组">
      {SETTING_GROUPS.map((g) => (
        <a
          key={g.id}
          className={clsx(active === g.id && 'active')}
          data-action="设置导航"
          onClick={() => document.getElementById(g.id)?.scrollIntoView({ behavior: 'smooth' })}
        >
          {g.label}
        </a>
      ))}
    </nav>
  );
}
