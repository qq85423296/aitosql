/**
 * AiReviewPanel —— 统一工作区「评审建议」视图（2026-11-05 计划 §3.3/§5 阶段 3）。
 *
 * - 顶部「本批建议 / 全部待评审」：本批严格限定当前运行 run_id，全部清除批次限制；
 *   表入口默认叠加当前表过滤，「查看本库全部」才清除；
 * - 卡片：对象 / 现值→建议值 / 依据（缺失明示「暂无依据」）/ 置信度（辅助，不参与自动决策）；
 * - 单条「采纳 / 编辑后采纳（仅 comment，后端唯一支持编辑的类型）/ 忽略」；
 *   多选后「采纳所选 / 忽略所选」，仅作用于明确选中 ID；过滤变更清除不可见选中项；
 * - 「忽略全部」非主操作：二次确认并明示影响范围（当前列表 N 条）；
 * - 成功：刷新建议/表/字段/关系/图谱/指标缓存与本批·全部计数；失败：保留选中与编辑状态，显示错误；
 * - P5 边界：建议未经采纳不进入正式元数据（采纳映射见 backend store/suggestions.py）。
 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { suggestionsApi } from '../../api/suggestions';
import { aiRunsApi } from '../../api/aiRuns';
import { datasourcesApi } from '../../api/datasources';
import type { AiSuggestion, SuggestionKind } from '../../api/types';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';

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

/** payload_json → 人读文本（复用旧抽屉逐字文案，不编造现值或依据） */
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

export function AiReviewPanel({
  datasourceId,
  batchRunId,
  mode,
  onModeChange,
  defaultTable,
  onGoGenerate,
}: {
  datasourceId: number;
  /** 本会话最近完成且有产出的 run_id（「本批建议」）；null 时本批页签不展示 */
  batchRunId: number | null;
  mode: 'batch' | 'all';
  onModeChange: (m: 'batch' | 'all') => void;
  /** 表入口：默认表过滤（「查看本库全部」才清除） */
  defaultTable: string | null;
  onGoGenerate: () => void;
}) {
  const qc = useQueryClient();
  const [table, setTable] = useState<string | null>(defaultTable);
  const [kindFilter, setKindFilter] = useState<SuggestionKind | null>(null);
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<Record<number, string>>({});
  const [ignoreAllConfirm, setIgnoreAllConfirm] = useState(false);

  // 表入口切换：默认表过滤跟随入口，并清空选中（防跨表误操作）
  useEffect(() => {
    setTable(defaultTable);
    setChecked(new Set());
    setEditing({});
  }, [defaultTable]);

  // 本批建议 = 当前运行 run_id；全部待评审 = 清除批次限制（高级筛选里的批次选择是手动覆盖）
  const effectiveRunId = mode === 'batch' ? batchRunId : runFilter;

  const { data } = useQuery({
    queryKey: ['suggestions', datasourceId, 'ws', effectiveRunId, table],
    queryFn: () => suggestionsApi.list(datasourceId, 'pending', { runId: effectiveRunId, table }),
  });
  const { data: runs } = useQuery({ queryKey: ['aiRuns', datasourceId], queryFn: () => aiRunsApi.list(datasourceId) });
  const { data: tables } = useQuery({ queryKey: ['tables', datasourceId], queryFn: () => datasourcesApi.tables(datasourceId) });

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
    () => (kindFilter ? ({ [kindFilter]: groups[kindFilter] } as Record<SuggestionKind, AiSuggestion[]>) : groups),
    [groups, kindFilter],
  );
  const total = (Object.values(visibleGroups) as AiSuggestion[][]).flat().length;
  const allIds = useMemo(() => (Object.values(visibleGroups) as AiSuggestion[][]).flat().map((s) => s.id), [visibleGroups]);

  // 过滤/列表变更后清除不在当前可见列表的选中项（防批量操作波及隐藏建议）
  useEffect(() => {
    setChecked((prev) => {
      const visible = new Set(allIds);
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [allIds]);

  /** 成功收尾：刷新本批/全部计数与目标缓存（表/字段/图谱/关系/指标）；清空选中与编辑 */
  const done = (text: string) => {
    setChecked(new Set());
    setEditing({});
    void qc.invalidateQueries({ queryKey: ['suggestions', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['tables', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['table'] });
    void qc.invalidateQueries({ queryKey: ['relations', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['graph', datasourceId] });
    void qc.invalidateQueries({ queryKey: ['metrics'] });
    toast(text, 'ok');
  };
  // 失败：保留选中与编辑状态，仅显示错误（不产生假成功反馈）
  const acceptMut = useMutation({
    mutationFn: (ids: number[]) => {
      const edits = Object.fromEntries(Object.entries(editing).filter(([id]) => ids.includes(Number(id))));
      return suggestionsApi.accept(ids, Object.keys(edits).length ? (edits as Record<number, string>) : undefined);
    },
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

  const hasFilter = effectiveRunId != null || table != null || kindFilter != null;
  const busy = acceptMut.isPending || ignoreMut.isPending;

  return (
    <div className="ai-ws-review">
      {/* 视图切换：本批（当前 run_id）/ 全部待评审（无批次限制） */}
      <div className="ai-ws-review-head">
        <div className="tabbar" role="tablist" style={{ marginBottom: 0, borderBottom: 'none' }}>
          {batchRunId != null && (
            <div
              role="tab"
              aria-selected={mode === 'batch'}
              className={`tab-item${mode === 'batch' ? ' active' : ''}`}
              data-action="切到本批建议"
              onClick={() => onModeChange('batch')}
            >
              本批建议<span className="cnt">#{batchRunId}</span>
            </div>
          )}
          <div
            role="tab"
            aria-selected={mode === 'all'}
            className={`tab-item${mode === 'all' ? ' active' : ''}`}
            data-action="切到全部待评审"
            onClick={() => onModeChange('all')}
          >
            全部待评审
          </div>
        </div>
        {defaultTable != null &&
          (table === defaultTable ? (
            <span className="ai-ws-chip">
              仅当前表 {defaultTable}
              <span className="row-act" data-action="查看本库全部" onClick={() => setTable(null)}>
                查看本库全部
              </span>
            </span>
          ) : (
            <span className="row-act" data-action="仅看当前表" onClick={() => setTable(defaultTable)}>
              仅看当前表 {defaultTable}
            </span>
          ))}
        <span className="row-act" style={{ marginLeft: 'auto' }} data-action="展开筛选" onClick={() => setFiltersOpen((v) => !v)}>
          筛选 {filtersOpen ? '▴' : '▾'}
        </span>
      </div>

      {/* 批次/表/类型过滤（默认折叠） */}
      {filtersOpen && (
        <div className="sg-filter-bar">
          {mode === 'all' && (
            <select
              className="input sg-filter-sel"
              value={runFilter ?? ''}
              data-action="按批次过滤"
              onChange={(e) => setRunFilter(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">全部批次</option>
              {(runs ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  任务 #{r.id}（{r.status === 'done' ? '已完成' : r.status}）
                </option>
              ))}
            </select>
          )}
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
          {hasFilter && mode === 'all' && (
            <span
              className="row-act"
              data-action="清除过滤"
              onClick={() => {
                setRunFilter(null);
                setTable(null);
                setKindFilter(null);
              }}
            >
              清除过滤
            </span>
          )}
        </div>
      )}

      <div className="muted" style={{ margin: '8px 0' }}>
        AI 只建议、不落库；采纳后写入对应实体。
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
                        {/* 现值 → 建议值 diff（comment/std_bind；payload.current 由后端随建议落库） */}
                        {(s.kind === 'comment' || s.kind === 'std_bind') && p.current != null && (
                          <div className="sg-diff">
                            现值 <span className={p.current && p.current !== '（未绑定）' ? 'sg-diff-old' : 'muted'}>{p.current || '（空）'}</span>
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
                        {/* 依据行：缺失时明确「暂无依据」，不编造 */}
                        <div className="sg-reason">
                          依据：{p.reason || (p.via === 'name_match' ? '命名惯例/主外键同名同型（规则推断）' : p.via) || <span className="muted">暂无依据</span>}
                        </div>
                        <div className="sg-meta">
                          <span className={`sg-kind ${s.kind}`}>{KIND_LABEL[s.kind]}</span>
                          <span className="sg-conf">置信度 {s.confidence.toFixed(2)}</span>
                          {s.run_id != null && <span className="muted" style={{ fontSize: 10 }}>批次 #{s.run_id}</span>}
                        </div>
                        {/* 单条显式动作：采纳 / 编辑后采纳（仅 comment）/ 忽略 */}
                        <div className="sg-actions" onClick={(e) => e.stopPropagation()}>
                          <span className="row-act accent" data-action="单条采纳" onClick={() => !busy && acceptMut.mutate([s.id])}>
                            采纳
                          </span>
                          {s.kind === 'comment' &&
                            (isEditing ? (
                              <span
                                className="row-act"
                                data-action="撤销编辑"
                                onClick={() =>
                                  setEditing((prev) => {
                                    const n = { ...prev };
                                    delete n[s.id];
                                    return n;
                                  })
                                }
                              >
                                撤销编辑
                              </span>
                            ) : (
                              <span
                                className="row-act"
                                data-action="进入编辑"
                                onClick={() => setEditing((prev) => ({ ...prev, [s.id]: p.suggested ?? '' }))}
                              >
                                编辑
                              </span>
                            ))}
                          {isEditing && (
                            <span className="row-act accent" data-action="编辑后采纳" onClick={() => !busy && acceptMut.mutate([s.id])}>
                              编辑后采纳
                            </span>
                          )}
                          <span className="row-act danger" data-action="单条忽略" onClick={() => !busy && ignoreMut.mutate([s.id])}>
                            忽略
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ),
      )}

      {total === 0 &&
        (mode === 'batch' && batchRunId != null ? (
          <div className="ai-ws-state">
            <div className="big">本批建议已全部处理 ✓</div>
            <div className="muted" style={{ marginTop: 4 }}>批次 #{batchRunId} 已清空；其余历史待评审建议不受影响。</div>
            <div className="modal-foot" style={{ justifyContent: 'center' }}>
              <Button variant="ghost" data-action="本批清空-查看全部" onClick={() => onModeChange('all')}>
                查看全部待评审
              </Button>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            {hasFilter ? '过滤条件下暂无建议' : '暂无待评审建议'}
            {!hasFilter && (
              <div style={{ marginTop: 10 }}>
                <Button variant="ghost" size="sm" data-action="空态-去生成建议" onClick={onGoGenerate}>
                  去生成建议
                </Button>
              </div>
            )}
          </div>
        ))}

      {/* 批量操作条：多选后出现；「忽略全部」非主操作，二次确认并明示范围 */}
      <div className="ai-ws-batchbar">
        <span className="dp-count">
          已选 {checked.size} · 剩余 {total} 条
        </span>
        {checked.size > 0 && (
          <>
            <Button variant="ghost" size="sm" data-action="忽略所选" disabled={busy} onClick={() => ignoreMut.mutate([...checked])}>
              忽略所选
            </Button>
            <Button variant="accent" size="sm" data-action="采纳所选" disabled={busy} onClick={() => acceptMut.mutate([...checked])}>
              采纳所选
            </Button>
          </>
        )}
        {allIds.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            data-action="忽略全部"
            disabled={busy}
            onClick={() => {
              if (ignoreAllConfirm) {
                ignoreMut.mutate(allIds);
              } else {
                setIgnoreAllConfirm(true);
                setTimeout(() => setIgnoreAllConfirm(false), 3000);
              }
            }}
          >
            {ignoreAllConfirm ? `确认忽略当前列表全部 ${allIds.length} 条？` : '忽略全部'}
          </Button>
        )}
      </div>
    </div>
  );
}
