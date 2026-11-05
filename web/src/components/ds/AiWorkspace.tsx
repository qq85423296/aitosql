/**
 * AiWorkspace —— AI 补全统一工作区框架（2026-11-05 计划 §4.2）。
 *
 * - 单实例：由 DatasourcesPage 统一托管（按 ctx.datasourceId 作 key，切数据源整体重建，防跨源串数据）；
 * - 「生成建议 / 评审建议」两个视图共享同一任务控制器（useAiCompletionRun）；
 * - 抽屉关闭只改可见性（组件保持挂载，SSE/轮询继续）；离开路由由 hook 清理释放连接，不发取消；
 * - 任务 done 且有产出 → 自动切「评审建议 · 本批建议」（严格 run_id）。
 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { suggestionsApi } from '../../api/suggestions';
import type { AiRun } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { useAiCompletionRun } from './useAiCompletionRun';
import { AiGenerationPanel } from './AiGenerationPanel';
import { AiReviewPanel } from './AiReviewPanel';

/** 工作区入口上下文（数据源详情 / 表详情 / 表列表行共用） */
export interface AiWorkspaceEntry {
  datasourceId: number;
  /** 表入口：当前表（生成默认 scope=table；评审默认按表过滤） */
  tableId?: number;
  tableName?: string;
  initialView: 'generate' | 'review';
}

export function AiWorkspace({ ctx, open, onClose }: { ctx: AiWorkspaceEntry; open: boolean; onClose: () => void }) {
  const [view, setView] = useState<'generate' | 'review'>(ctx.initialView);
  const [reviewMode, setReviewMode] = useState<'batch' | 'all'>('all');
  /** 本会话最近完成且产出 >0 的任务（「本批建议」严格按此 run_id） */
  const [batchRunId, setBatchRunId] = useState<number | null>(null);

  const ctl = useAiCompletionRun(ctx.datasourceId, (final: AiRun) => {
    if (final.status !== 'done') return;
    const total = Object.values(final.made ?? {}).reduce((a, b) => a + b, 0);
    if (total > 0) {
      setBatchRunId(final.id);
      setReviewMode('batch');
      setView('review'); // 完成且有新建议：自动切「本批建议」
    }
  });

  // 入口上下文变化（再次点击入口/换表）：视图与评审默认值跟随最新入口意图
  useEffect(() => {
    setView(ctx.initialView);
    if (ctx.initialView === 'review') setReviewMode('all');
  }, [ctx]);

  const { data: dsList = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const ds = dsList.find((d) => d.id === ctx.datasourceId);
  const { data: sgData } = useQuery({
    queryKey: ['suggestions', ctx.datasourceId],
    queryFn: () => suggestionsApi.list(ctx.datasourceId, 'pending'),
  });
  const { data: tables = [] } = useQuery({
    queryKey: ['tables', ctx.datasourceId],
    queryFn: () => datasourcesApi.tables(ctx.datasourceId),
  });

  const pendingTotal = sgData?.total ?? 0;
  const running = ctl.run != null && (ctl.run.status === 'queued' || ctl.run.status === 'running');
  const dsName = ds?.name ?? `数据源 #${ctx.datasourceId}`;

  return (
    <Drawer
      open={open}
      full
      title={
        <>
          AI 完善 · {dsName}
          {ctx.tableName && <span className="mono-cell" style={{ color: 'var(--t3)' }}> / {ctx.tableName}</span>}
        </>
      }
      onClose={onClose}
    >
      <div className="tabbar" style={{ marginTop: 0 }}>
        <div
          role="tab"
          aria-selected={view === 'generate'}
          className={`tab-item${view === 'generate' ? ' active' : ''}`}
          data-action="工作区切到生成建议"
          onClick={() => setView('generate')}
        >
          生成建议
          {running && <span className="cnt">进行中</span>}
        </div>
        <div
          role="tab"
          aria-selected={view === 'review'}
          className={`tab-item${view === 'review' ? ' active' : ''}`}
          data-action="工作区切到评审建议"
          onClick={() => setView('review')}
        >
          评审建议
          <span className="cnt">{pendingTotal}</span>
        </div>
      </div>

      {view === 'generate' ? (
        <AiGenerationPanel
          datasourceId={ctx.datasourceId}
          tableId={ctx.tableId}
          tableName={ctx.tableName}
          online={ds?.mode === 'online'}
          totalTables={tables.length || ds?.table_count || 0}
          ctl={ctl}
          onCloseWorkspace={onClose}
          onGoReviewAll={() => {
            setReviewMode('all');
            setView('review');
          }}
          onReviewBatch={(rid) => {
            setBatchRunId(rid);
            setReviewMode('batch');
            setView('review');
          }}
        />
      ) : (
        <AiReviewPanel
          datasourceId={ctx.datasourceId}
          batchRunId={batchRunId}
          mode={reviewMode}
          onModeChange={setReviewMode}
          defaultTable={ctx.tableName ?? null}
          onGoGenerate={() => setView('generate')}
        />
      )}
    </Drawer>
  );
}
