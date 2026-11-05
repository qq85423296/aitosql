/** sessionStore —— 仅 UI 态（V4）：当前会话、抽屉开合、过滤词、当前数据源选择 */
import { create } from 'zustand';

interface SessionUiState {
  currentSessionId: string | null;
  drawerOpen: boolean;
  filter: string;
  currentDsId: number | null;
  contextPanelOpen: boolean;
  contextTab: 'schema' | 'detail';
  composerDraft: string;
  composerPrefill: string | null; // F4 修正假设/F3 自定义口径预填

  setCurrentSession: (id: string | null) => void;
  toggleDrawer: () => void;
  setDrawerOpen: (v: boolean) => void;
  setFilter: (v: string) => void;
  setCurrentDs: (id: number | null) => void;
  toggleContextPanel: () => void;
  setContextTab: (t: 'schema' | 'detail') => void;
  setComposerDraft: (v: string) => void;
  prefillComposer: (v: string) => void;
  clearPrefill: () => void;
}

export const useSessionStore = create<SessionUiState>((set) => ({
  currentSessionId: null,
  drawerOpen: true,
  filter: '',
  currentDsId: null,
  contextPanelOpen: true,
  contextTab: 'schema',
  composerDraft: '',
  composerPrefill: null,

  setCurrentSession: (id) => set({ currentSessionId: id }),
  toggleDrawer: () => set((s) => ({ drawerOpen: !s.drawerOpen })),
  setDrawerOpen: (v) => set({ drawerOpen: v }),
  setFilter: (v) => set({ filter: v }),
  setCurrentDs: (id) => set({ currentDsId: id }),
  toggleContextPanel: () => set((s) => ({ contextPanelOpen: !s.contextPanelOpen })),
  setContextTab: (t) => set({ contextTab: t }),
  setComposerDraft: (v) => set({ composerDraft: v }),
  prefillComposer: (v) => set({ composerPrefill: v }),
  clearPrefill: () => set({ composerPrefill: null }),
}));
