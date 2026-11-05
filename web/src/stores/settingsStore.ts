/** settingsStore —— 仅 UI 态（V4）：主题、面板开关；设置键值本身归 TanStack Query */
import { create } from 'zustand';

export type Theme = 'dark' | 'light';
const THEME_KEY = 'aitosql.theme';

interface SettingsUiState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === 'light' ? 'light' : 'dark'; // 深色默认（§4.2）
}

export const useSettingsStore = create<SettingsUiState>((set, get) => ({
  theme: initialTheme(),
  setTheme: (t) => {
    localStorage.setItem(THEME_KEY, t);
    document.documentElement.setAttribute('data-theme', t);
    set({ theme: t });
  },
  toggleTheme: () => {
    const next = get().theme === 'light' ? 'dark' : 'light';
    get().setTheme(next);
  },
}));
