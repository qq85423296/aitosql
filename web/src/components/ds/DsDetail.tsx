import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { relationsApi } from '../../api/relations';
import { standardsApi, dimensionsApi, metricsApi } from '../../api/standards';
import { suggestionsApi } from '../../api/suggestions';
import { tablesApi } from '../../api/tables';
import type { Datasource } from '../../api/types';
import { DialectBadge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { TableList } from './TableList';
import { TableDetail } from './TableDetail';
import { RelGraph } from './RelGraph';
import { StandardsTab } from './StandardsTab';
import { DimsTab } from './DimsTab';
import { MetricsTab } from './MetricsTab';
import type { AiWorkspaceEntry } from './AiWorkspace';
import { EditDsDrawer, reingestDs } from './DsList';
import { fmtInt, fmtRel } from '../../lib/format';

/**
 * DsDetail（FR-D5~D12，原型视图 3）：面包屑 + 标题行 + ds-meta 统计行 + 5 Tab（带计数徽章）；
 * 表 Tab 内嵌表详情（视图 4）。
 * AI 统一工作区（2026-11-05 计划 §4.4）：标题行只保留两个入口——主按钮「AI 完善数据源」与
 * 待办按钮「待评审建议 N 条」（N>0 时展示）；不再挂 AiCompleteModal/SuggestionDrawer 双层弹层，
 * 统一打开 DatasourcesPage 托管的工作区。
 */
export function DsDetail({
  datasourceId,
  onBack,
  onOpenAiWorkspace,
}: {
  datasourceId: number;
  onBack: () => void;
  onOpenAiWorkspace: (entry: AiWorkspaceEntry) => void;
}) {
  const qc = useQueryClient();
  const { data: list = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const ds = list.find((d) => d.id === datasourceId);
  const [tab, setTab] = useState('tables');
  const [tableId, setTableId] = useState<number | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  // Tab 计数（与子组件共用 react-query 缓存，零额外请求）
  const { data: tables = [] } = useQuery({ queryKey: ['tables', datasourceId], queryFn: () => datasourcesApi.tables(datasourceId) });
  const { data: relations = [] } = useQuery({ queryKey: ['relations', datasourceId], queryFn: () => relationsApi.list(datasourceId) });
  const { data: standards = [] } = useQuery({ queryKey: ['standards'], queryFn: standardsApi.list });
  const { data: dims = [] } = useQuery({ queryKey: ['dimensions'], queryFn: dimensionsApi.list });
  const { data: metrics = [] } = useQuery({ queryKey: ['metrics'], queryFn: metricsApi.list });
  const { data: sgData } = useQuery({ queryKey: ['suggestions', datasourceId], queryFn: () => suggestionsApi.list(datasourceId, 'pending') });

  if (!ds) return <div className="empty-state">数据源不存在或已删除</div>;
  const sgTotal = sgData?.total ?? 0;

  // 原型视图切换语义：表详情是独立整视图，替换数据源详情全部内容
  if (tableId != null && tab === 'tables') {
    return (
      <TableDetailView
        datasourceId={datasourceId}
        dsName={ds.name}
        tableId={tableId}
        onBack={() => setTableId(null)}
        onBackToList={onBack}
        onOpenAiWorkspace={onOpenAiWorkspace}
      />
    );
  }

  return (
    <div>
      <div className="crumb">
        <a data-action="返回数据源列表" onClick={onBack}>数据源</a>
        <span>/</span>
        <span style={{ color: 'var(--t1)' }}>{ds.name}</span>
      </div>
      <div className="page-head" style={{ marginTop: 10 }}>
        <span className="page-title">{ds.name}</span>
        <DialectBadge dialect={ds.dialect} />
        <div className="head-actions">
          <Button
            variant="ai"
            size="sm"
            data-action="AI 完善数据源"
            onClick={() => onOpenAiWorkspace({ datasourceId, initialView: 'generate' })}
          >
            <Icon name="spark" size={13} />
            AI 完善数据源
          </Button>
          {sgTotal > 0 && (
            <Button
              variant="ghost"
              size="sm"
              data-action="打开待评审建议"
              onClick={() => onOpenAiWorkspace({ datasourceId, initialView: 'review' })}
            >
              待评审建议 {sgTotal} 条
            </Button>
          )}
          <Button variant="ghost" size="sm" data-action="编辑连接" onClick={() => setEditOpen(true)}>
            编辑连接
          </Button>
          {ds.mode === 'online' && (
            <Button variant="ghost" size="sm" data-action="重新摄取" onClick={() => reingestDs(ds, qc)}>
              <Icon name="refresh" size={13} strokeWidth={2} />
              重新摄取
            </Button>
          )}
        </div>
      </div>
      <DsMeta ds={ds} tableCount={tables.length || ds.table_count} fieldCount={ds.field_count} />

      <div className="tabbar">
        {[
          ['tables', '表', tables.length],
          ['graph', '关系图谱', relations.length],
          ['standards', '字段标准', standards.length],
          ['dims', '维度标准', dims.length],
          ['metrics', '指标口径', metrics.length],
        ].map(([key, label, cnt]) => (
          <div
            key={key as string}
            role="tab"
            aria-selected={tab === key}
            className={`tab-item${tab === key ? ' active' : ''}`}
            data-action="详情页签切换"
            onClick={() => {
              setTab(key as string);
              setTableId(null);
            }}
          >
            {label as string}
            <span className="cnt">{cnt as number}</span>
          </div>
        ))}
      </div>

      {tab === 'tables' && <TableList datasourceId={datasourceId} onOpenTable={setTableId} onOpenAiWorkspace={onOpenAiWorkspace} />}
      {tab === 'graph' && (
        <RelGraph
          datasourceId={datasourceId}
          onOpenTable={(id) => {
            setTab('tables');
            setTableId(id);
          }}
        />
      )}
      {tab === 'standards' && <StandardsTab />}
      {tab === 'dims' && <DimsTab />}
      {tab === 'metrics' && <MetricsTab />}

      <EditDsDrawer ds={editOpen ? ds : null} onClose={() => setEditOpen(false)} />
    </div>
  );
}

/** ds-meta 统计行：host/db · 版本 · 表数 · 字段数 · 上次摄取（sep2 分隔） */
function DsMeta({ ds, tableCount, fieldCount }: { ds: Datasource; tableCount?: number; fieldCount?: number | null }) {
  return (
    <div className="ds-meta">
      <span className="host">
        {ds.mode === 'online' ? `${ds.host ?? ''}${ds.port ? `:${ds.port}` : ''}${ds.db_name ? ` / ${ds.db_name}` : ''}` : `离线库 · ${ds.db_name ?? ''}`}
      </span>
      <span className="sep2"></span>
      <span>
        版本{' '}
        <b className="num" style={{ color: ds.version ? 'var(--green)' : undefined }}>
          {ds.version ?? '—'}
        </b>
      </span>
      <span className="sep2"></span>
      <span>
        <b className="num">{fmtInt(tableCount)}</b> 表
      </span>
      <span className="sep2"></span>
      <span>
        <b className="num">{fmtInt(fieldCount)}</b> 字段
      </span>
      <span className="sep2"></span>
      <span>上次摄取 {fmtRel(ds.last_ingest_at)}</span>
    </div>
  );
}

/** 表详情视图（原型视图 4）：GET /tables/:id 包络解包后渲染 TableDetail；透传工作区打开回调 */
function TableDetailView({
  datasourceId,
  dsName,
  tableId,
  onBack,
  onBackToList,
  onOpenAiWorkspace,
}: {
  datasourceId: number;
  dsName: string;
  tableId: number;
  onBack: () => void;
  onBackToList: () => void;
  onOpenAiWorkspace: (entry: AiWorkspaceEntry) => void;
}) {
  const { data: detail } = useQuery({ queryKey: ['table', tableId], queryFn: () => tablesApi.get(tableId) });
  if (!detail) return <div className="empty-state">表加载中…</div>;
  return (
    <TableDetail
      table={detail.table}
      columns={detail.columns}
      datasourceId={datasourceId}
      dsName={dsName}
      onBack={onBack}
      onBackToList={onBackToList}
      onOpenAiWorkspace={onOpenAiWorkspace}
    />
  );
}
