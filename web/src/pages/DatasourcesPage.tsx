import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { settingsApi } from '../api/settings';
import { DsList } from '../components/ds/DsList';
import { DsWizard } from '../components/ds/DsWizard';
import { DsDetail } from '../components/ds/DsDetail';
import { AiWorkspace, type AiWorkspaceEntry } from '../components/ds/AiWorkspace';
import { DsSwitcher } from '../components/query/DsSwitcher';
import { Icon } from '../components/ui/icons';
import { useSessionStore } from '../stores/sessionStore';
import { parseRouteRef } from '../lib/format';

/**
 * DatasourcesPage（FR-D1~D13，原型骨架）：顶栏（ds-switch + route-pill）+
 * 3 互斥视图 list / wizard / ds 详情（table 详情内嵌于 DsDetail 的表 Tab 内）。
 * AI 统一工作区（2026-11-05 计划 §4.1）：页面持有唯一工作区打开状态与入口上下文；
 * 切换详情内部视图不销毁工作区控制器；离开路由时组件卸载、释放 SSE 但不发取消请求。
 */
export function DatasourcesPage() {
  const [view, setView] = useState<'list' | 'wizard' | 'detail'>('list');
  const [dsId, setDsId] = useState<number | null>(null);
  const currentDsId = useSessionStore((s) => s.currentDsId);
  const setCurrentDs = useSessionStore((s) => s.setCurrentDs);
  const { data: settingsBundle } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const routeModel = parseRouteRef(settingsBundle?.settings?.['route.generate'] as string)?.model ?? '…';
  const localOnly = settingsBundle?.local_only ?? true;

  // 唯一 AI 工作区：ctx 记录入口上下文；open=false 时仅隐藏（控制器保持存活，后台任务续跑）
  const [ws, setWs] = useState<{ ctx: AiWorkspaceEntry; open: boolean } | null>(null);
  const openWorkspace = useCallback((ctx: AiWorkspaceEntry) => setWs({ ctx, open: true }), []);
  const closeWorkspace = useCallback(() => setWs((prev) => (prev ? { ...prev, open: false } : null)), []);

  return (
    <div className="main-col">
      <header className="topbar">
        <DsSwitcher value={currentDsId} onChange={setCurrentDs} />
        <div className="topbar-right" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="route-pill">
            <Icon name="lock" size={13} strokeWidth={2} />
            {localOnly ? '仅本地' : '云端'} · {routeModel}
            <span className="dot"></span>
          </span>
        </div>
      </header>
      <div className="page-scroll">
        <div className="page-inner" style={view === 'wizard' ? { maxWidth: 960 } : undefined}>
          {view === 'list' && (
            <DsList
              onOpenDetail={(id) => {
                setDsId(id);
                setView('detail');
              }}
              onOpenWizard={() => setView('wizard')}
            />
          )}
          {view === 'wizard' && (
            <DsWizard
              onDone={(id) => {
                setDsId(id);
                setView('detail');
              }}
              onCancel={() => setView('list')}
            />
          )}
          {view === 'detail' && dsId != null && (
            <DsDetail datasourceId={dsId} onBack={() => setView('list')} onOpenAiWorkspace={openWorkspace} />
          )}
        </div>
      </div>
      {ws && (
        <AiWorkspace
          key={ws.ctx.datasourceId}
          ctx={ws.ctx}
          open={ws.open && view === 'detail' && dsId === ws.ctx.datasourceId}
          onClose={closeWorkspace}
        />
      )}
    </div>
  );
}
