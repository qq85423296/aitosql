import { useEffect } from 'react';
import { useSettingsStore } from '../stores/settingsStore';

/** 主题引导：localStorage 记忆 + data-theme 属性（tokens.css 双主题块） */
export function useTheme() {
  const theme = useSettingsStore((s) => s.theme);
  const toggleTheme = useSettingsStore((s) => s.toggleTheme);
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);
  return { theme, toggleTheme };
}
