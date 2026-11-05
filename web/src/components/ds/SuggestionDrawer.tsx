import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { suggestionsApi } from '../../api/suggestions';
import { aiRunsApi } from '../../api/aiRuns';
import { datasourcesApi } from '../../api/datasources';
import type { AiSuggestion, SuggestionKind } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import { AiCompleteModal } from './AiCompleteModal';

const GROUP_LABEL: Record<SuggestionKind, string> = {
  comment: '注释补全',
  std_bind: '标准绑定建议',
  relation: '关系确认',
  metric: '指标归纳',
};
const KIND_LABEL: Record<SuggestionKind, string> = {
  comment: '注释',
  std_bind: '标准',
  relation: '关系',
  metric: '指标',
};

interface SgPayload {
  table?: string;
  column?: string;
  suggested?: string;
  standard?: string;
  from?: string;
  to?: string;
  via?: string;
  note?: string;
  name?: string;
  expr?: string;
  reason?: string;
  current?: string;
}

function parsePayload(s: AiSuggestion): SgPayload {
  try {
    return JSON.parse(s.payload_json) as SgPayload;
  } catch {
    return {};
  }
}

/** payload_json → 人读文本（含 code 片段） */
function sgText(s: AiSuggestion, p: SgPayload): React.ReactNode {
  switch (s.kind) {
    case 'comment':
      return p.column ? (
        <>
          <code>{p.table}.{p.column}</code> → 建议注释：{p.suggested}
        </>
      ) : (
        <>
          表 <code>{p.table}</code> 缺注释 → 建议：{p.suggested}
        </>
      );
    case 'std_bind':
      return (
        <>
          <code>{p.table}.{p.column}</code> 建议绑定标准 <b>{p.standard}</b>
          {p.note ? `（${p.note}）` : ''}
        </>
      );
    case 'relation':
      return (
        <>
          确认推测关系 <code>{p.from} → {p.to}</code>
        </>
      );
    case 'metric':
      return (
        <>
          从样例 SQL 归纳指标：<code>{p.name}</code> = {p.expr}
        </>
      );
  }
}

/**
 * SuggestionDrawer（FR-D12 建议队列，原型 AI 补全抽屉）：四类分组 sg-item 勾选评审 /
 * 批量采纳·忽略 / 全部采纳。采纳映射（C6.8/C6.8+）：comment→comment_custom（column=""→表级）；
 * std_bind→field_standard_id；relation→relation(confirmed)；metric→metric_standard。
 * 方案 v1.1 §7 增量：批次/表/类型过滤 · 依据行（reason）· 现值→建议值 diff · comment 编辑后采纳。
 */
export function SuggestionDrawer({
  datasourceId,
  open,
  onClose,
  filterRunId = null,
  filterTable = null,
}: {
  datasourceId: number;
  open: boolean;
  onClose: () => void;
  filterRunId?: number | null;
  filterTable?: string | null;
}) {
  const qc = useQueryClient();
  const [runId, setRunId] = useState<number | null>(filterRunId);
  const [table, setTable] = useState<string | null>(filterTable);
  const [kindFilter, setKindFilter] = useState<SuggestionKind | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<Record<number, string>>({});
  const [ignoreAllConfirm, setIgnoreAllConfirm] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setRunId(filterRunId);
      setTable(filterTable);
      setChecked(new Set());
      setEditing({});
    }
  }, [open, filterRunId, filterTable]);

  // C1.3 冻结包络（P1-6）：{groups, total}——后端已按 kind 分组，直接消费
  const { data } = useQuery({
    queryKey: ['suggestions', datasourceId, runId, table],
    queryFn: () => suggestionsApi.list(datasourceId, 'pending', { runId, table }),
    enabled: open,
  });
  const { data: runs } = useQuery({
    queryKey: ['aiRuns', datasourceId],
    queryFn: () => aiRunsApi.list(datasourceId),
    enabled: open,
  });
  const { data: tables } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId),
    enabled: open,
  });
  // 数据源名/接入方式（重新生成模态用）
  const { data: dsList } = useQuery({
    queryKey: ['datasources'],
    queryFn: datasourcesApi.list,
    enabled: open,
  });
  const ds = (dsList ?? []).find((d) => d.id === datasourceId);

  const groups = useMemo<Record<SuggestionKind, AiSuggestion[]>>(
    () => ({
      comment: data?.groups.comment ?? [],
      std_bind: data?.groups.std_bind ?? [],
      relation: data?.groups.relation ?? [],
      metric: data?.groups.metric ?? [],
    }),
    [data],
  );
  const visibleGroups = useMemo(
    () =>
      kindFilter
        ? ({ [kindFilter]: groups[kindFilter] } as Record<SuggestionKind, AiSuggestion[]>)
        : groups,
    [groups, kindFilter],
  );
  const total = (Object.values(visibleGroups) as AiSuggestion[][]).flat().length;
  const allIds = useMemo(
    () => (Object.values(visibleGroups) as AiSuggestion[][]).flat().map((s) => s.id),
    [visibleGroups],
  );

  const done = (text: string) => {
    setChecked(new Set());
    setEditing({});
    void qc.invalidateQueries({ queryKey: ['suggestions', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['tables', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['table'] });
    void qc.invalidateQueries({ queryKey: ['graph', datasourceId] });
    toast(text, 'ok');
  };
  const acceptMut = useMutation({
    mutationFn: (ids: number[]) =>
      suggestionsApi.accept(ids, Object.keys(editing).length ? editing : undefined),
    onSuccess: (r) => done(`已采纳 ${r.accepted} 条`),
    onError: (e) => toast(`采纳失败：${(e as Error).message}`, 'err'),
  });
  const ignoreMut = useMutation({
    mutationFn: (ids: number[]) => suggestionsApi.ignore(ids),
    onSuccess: (r) => {
      setIgnoreAllConfirm(false);
      done(`已忽略 ${r.ignored} 条（重跑补全不会再产出）`);
    },
    onError: (e) => toast(`忽略失败：${(e as Error).message}`, 'err'),
  });

  const toggle = (id: number) =>
    setChecked((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const hasFilter = runId != null || table != null || kindFilter != null;

  return (
    <Drawer
      open={open}
      full
      title="AI 建议评审"
      onClose={onClose}
      footer={
        <>
          <span className="dp-count">
            已选 {checked.size} · 剩余 {total} 条
          </span>
          <Button variant="ghost" data-action="重新生成建议" onClick={() => setAiOpen(true)}>
            重新生成
          </Button>
          <Button variant="ghost" data-action="忽略所选建议" disabled={!checked.size} onClick={() => ignoreMut.mutate([...checked])}>
            忽略所选
          </Button>
          <Button
            variant="ghost"
            data-action="忽略全部建议"
            disabled={!allIds.length}
            onClick={() => {
              if (ignoreAllConfirm) {
                ignoreMut.mutate(allIds);
              } else {
                setIgnoreAllConfirm(true);
                setTimeout(() => setIgnoreAllConfirm(false), 3000);
              }
            }}
          >
            {ignoreAllConfirm ? `确认忽略全部 ${allIds.length} 条？` : '忽略全部'}
          </Button>
          <Button variant="ghost" data-action="暂不处理建议" onClick={onClose}>
            暂不处理
          </Button>
          <Button variant="ghost" data-action="全部采纳建议" disabled={!allIds.length} onClick={() => acceptMut.mutate(allIds)}>
            全部采纳
          </Button>
          <Button variant="accent" data-action="采纳建议" disabled={!checked.size} onClick={() => acceptMut.mutate([...checked])}>
            采纳所选
          </Button>
        </>
      }
    >
      <div className="muted" style={{ marginBottom: 8 }}>
        AI 只建议、不落库；采纳后写入对应实体。
      </div>

      {/* 过滤条（方案 §7）：批次 / 表 / 类型 */}
      <div className="sg-filter-bar">
        <select
          className="input sg-filter-sel"
          value={runId ?? ''}
          data-action="按批次过滤"
          onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">全部批次</option>
          {(runs ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              任务 #{r.id}（{r.status === 'done' ? '已完成' : r.status}）
            </option>
          ))}
        </select>
        <select
          className="input sg-filter-sel"
          value={table ?? ''}
          data-action="按表过滤"
          onChange={(e) => setTable(e.target.value || null)}
        >
          <option value="">全部表</option>
          {(tables ?? []).map((t) => (
            <option key={t.id} value={t.name}>
              {t.name}
            </option>
          ))}
        </select>
        <select
          className="input sg-filter-sel"
          value={kindFilter ?? ''}
          data-action="按类型过滤"
          onChange={(e) => setKindFilter((e.target.value || null) as SuggestionKind | null)}
        >
          <option value="">全部类型</option>
          {(Object.keys(GROUP_LABEL) as SuggestionKind[]).map((k) => (
            <option key={k} value={k}>
              {GROUP_LABEL[k]}
            </option>
          ))}
        </select>
        {hasFilter && (
          <span
            className="row-act"
            data-action="清除过滤"
            onClick={() => {
              setRunId(null);
              setTable(null);
              setKindFilter(null);
            }}
          >
            清除过滤
          </span>
        )}
      </div>

      {(Object.keys(visibleGroups) as SuggestionKind[]).map(
        (kind) =>
          visibleGroups[kind].length > 0 && (
            <div key={kind}>
              <div className="sg-group">
                {GROUP_LABEL[kind]}（{visibleGroups[kind].length}）
              </div>
              <div className="sg-grid">
              {visibleGroups[kind].map((s) => {
                const p = parsePayload(s);
                const isEditing = editing[s.id] != null;
                return (
                  <div key={s.id} className={`sg-item${checked.has(s.id) ? ' checked' : ''}`} data-action="勾选建议" onClick={() => toggle(s.id)}>
                    <span className="sg-check">
                      <Icon name="plus" size={11} strokeWidth={3} style={{ transform: 'rotate(45deg)', display: checked.has(s.id) ? 'none' : undefined }} />
                      {checked.has(s.id) && (
                        <svg viewBox="0 0 24 24" width={11} height={11} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
                          <path d="m4 12 6 6L20 6" />
                        </svg>
                      )}
                    </span>
                    <div className="sg-main">
                      <div className="sg-text">{sgText(s, p)}</div>
                      {/* 现值 → 建议值 diff（comment/std_bind，payload.current 由后端随建议落库） */}
                      {(s.kind === 'comment' || s.kind === 'std_bind') && p.current != null && (
                        <div className="sg-diff">
                          现值 <span className={p.current && p.current !== '（未绑定）' ? 'sg-diff-old' : 'muted'}>
                            {p.current || '（空）'}
                          </span>
                          {' → '}
                          <span className="sg-diff-new">{s.kind === 'std_bind' ? p.standard : p.suggested}</span>
                        </div>
                      )}
                      {isEditing && (
                        <input
                          className="input sg-edit-input"
                          value={editing[s.id]}
                          autoFocus
                          data-action="编辑建议文本"
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setEditing((prev) => ({ ...prev, [s.id]: e.target.value }))}
                        />
                      )}
                      {/* 依据行（方案 §5-2：reason 强制） */}
                      {(p.reason || p.via) && (
                        <div className="sg-reason">
                          依据：{p.reason || (p.via === 'name_match' ? '命名惯例/主外键同名同型（规则推断）' : p.via)}
                        </div>
                      )}
                      <div className="sg-meta">
                        <span className={`sg-kind ${s.kind}`}>{KIND_LABEL[s.kind]}</span>
                        <span className="sg-conf">置信度 {s.confidence.toFixed(2)}</span>
                        {s.run_id != null && <span className="muted" style={{ fontSize: 10 }}>批次 #{s.run_id}</span>}
                        {s.kind === 'comment' && !isEditing && (
                          <span
                            className="row-act"
                            style={{ padding: '1px 6px' }}
                            data-action="进入编辑"
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditing((prev) => ({ ...prev, [s.id]: p.suggested ?? '' }));
                            }}
                          >
                            编辑
                          </span>
                        )}
                        {isEditing && (
                          <span
                            className="row-act"
                            style={{ padding: '1px 6px' }}
                            data-action="取消编辑"
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditing((prev) => {
                                const n = { ...prev };
                                delete n[s.id];
                                return n;
                              });
                            }}
                          >
                            撤销编辑
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              </div>
            </div>
          ),
      )}
      {total === 0 && <div className="empty-state">{hasFilter ? '过滤条件下暂无建议' : '暂无待评审建议'}</div>}
      {/* 重新生成：补全流程模态（泳道可视化）；完成后按新批次过滤回本抽屉 */}
      <AiCompleteModal
        datasourceId={datasourceId}
        dsName={ds?.name ?? `数据源 #${datasourceId}`}
        scope="datasource"
        totalTables={tables?.length ?? 0}
        online={ds?.mode === 'online'}
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        onReview={(rid) => {
          setAiOpen(false);
          setRunId(rid);
          setTable(null);
          setKindFilter(null);
        }}
      />
    </Drawer>
  );
}
