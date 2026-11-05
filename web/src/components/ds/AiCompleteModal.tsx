import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { aiRunsApi } from '../../api/aiRuns';
import { suggestionsApi } from '../../api/suggestions';
import { settingsApi } from '../../api/settings';
import { datasourcesApi } from '../../api/datasources';
import type { AiSuggestion, CompletionKind, CompletionScopeType, LaneEvent } from '../../api/types';
import { mockEnabled } from '../../api/client';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';

const KIND_LABEL: Record<CompletionKind, string> = {
  comment: '注释补全',
  relation: '关系推断',
  std_bind: '标准绑定建议',
};

interface LaneState {
  table: string;
  phase: string;
  line: string;
}

/** 建议 payload → 一行可核对文本（目标 + 建议值；依据行另列） */
function suggestionLine(s: AiSuggestion): { target: string; value: string; reason: string | null } {
  let p: Record<string, string> = {};
  try {
    p = JSON.parse(s.payload_json) as Record<string, string>;
  } catch {
    return { target: s.kind, value: s.payload_json, reason: null };
  }
  const reason = p.reason || (p.via === 'name_match' ? '命名惯例/主外键同名同型（规则）' : p.via) || null;
  switch (s.kind) {
    case 'comment':
      return {
        target: p.column ? `${p.table}.${p.column}` : `表 ${p.table}`,
        value: p.suggested ?? '',
        reason,
      };
    case 'std_bind':
      return { target: `${p.table}.${p.column}`, value: `→ 标准「${p.standard}」`, reason };
    case 'relation':
      return { target: `${p.from} → ${p.to}`, value: '确认关系', reason };
    default:
      return { target: s.kind, value: p.suggested ?? p.name ?? '', reason };
  }
}

/**
 * AiCompleteModal（方案 v1.1 §7）：AI 补全配置 → 多泳道运行 → 完成评审。
 * scope 由入口决定：详情页头=整库 / 表详情=单表 / 图谱=仅关系（kinds 预设 relation）。
 */
export function AiCompleteModal({
  datasourceId,
  dsName,
  scope,
  tableId,
  tableName,
  totalTables,
  online,
  open,
  onClose,
  onReview,
}: {
  datasourceId: number;
  dsName: string;
  scope: CompletionScopeType;
  tableId?: number;
  tableName?: string;
  totalTables: number;
  online: boolean;
  open: boolean;
  onClose: () => void;
  /** 完成后「评审并采纳」：打开带 run 过滤的建议抽屉 */
  onReview: (runId: number | null) => void;
}) {
  const qc = useQueryClient();
  const [kinds, setKinds] = useState<CompletionKind[]>(
    scope === 'relations' ? ['relation'] : ['comment', 'relation', 'std_bind'],
  );
  const [rescan, setRescan] = useState(false);
  const [useSamples, setUseSamples] = useState(false);
  // 范围（datasource 入口可选）：整库 / 自选表（多选）
  const [scopeMode, setScopeMode] = useState<'all' | 'pick'>('all');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<'config' | 'running' | 'done'>('config');
  const [lanes, setLanes] = useState<Record<number, LaneState>>({});
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState({ done: 0, total: totalTables, pct: 0 });
  const [made, setMade] = useState<Record<string, number>>({});
  const [endState, setEndState] = useState<{ status: string; error?: string } | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const connRef = useRef<{ close: () => void } | null>(null);

  const canReview = phase === 'done' && Object.values(made).some((n) => n > 0);

  // route.ai 预检：未配置时注释/标准泳道必败——配置页提前拦截（关系推断不受影响）
  const { data: settingsData } = useQuery({
    queryKey: ['settings'],
    queryFn: settingsApi.get,
    enabled: open,
  });
  const aiRouteMissing = settingsData != null && !String(settingsData.settings['route.ai'] ?? '').trim();

  // 自选表清单（datasource 入口）
  const { data: tableRows } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId),
    enabled: open && scope === 'datasource',
  });
  const tableNames = useMemo(() => (tableRows ?? []).map((t) => t.name), [tableRows]);
  const toggleTable = (name: string) =>
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });
  const effectiveTotal = scope === 'datasource' && scopeMode === 'pick' ? picked.size : totalTables;

  // 完成视图产物清单：本批次建议明细（目标 + 建议值 + 依据 + 置信度），不离开弹窗即可核对
  const { data: produced } = useQuery({
    queryKey: ['suggestions', datasourceId, 'produced', runId],
    queryFn: () => suggestionsApi.list(datasourceId, 'pending', { runId }),
    enabled: phase === 'done' && runId != null,
  });
  const producedItems = useMemo(
    () => (Object.values(produced?.groups ?? {}) as AiSuggestion[][]).flat(),
    [produced],
  );

  useEffect(() => {
    if (!open) {
      connRef.current?.close();
      setPhase('config');
      setLanes({});
      setLogs([]);
      setMade({});
      setEndState(null);
      setRunId(null);
      setProgress({ done: 0, total: totalTables, pct: 0 });
    }
  }, [open, totalTables]);

  const toggleKind = (k: CompletionKind) =>
    setKinds((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  // route.ai 缺失时全部禁用（关系/注释/标准均为 AI 生成，无规则快道可兜底）
  useEffect(() => {
    if (aiRouteMissing) setKinds([]);
  }, [aiRouteMissing]);

  const start = () => {
    setPhase('running');
    setLanes({});
    setLogs([]);
    setMade({});
    setProgress({ done: 0, total: effectiveTotal, pct: 0 });
    const body = {
      datasource_id: datasourceId,
      scope: {
        type: scope,
        table_id: scope === 'table' ? tableId : undefined,
        table_names: scope === 'datasource' && scopeMode === 'pick' ? [...picked] : undefined,
      },
      kinds,
      options: { rescan_existing: rescan, use_sample_values: useSamples },
    };
    connRef.current = aiRunsApi.run(body, {
      onStart: (id) => {
        setRunId(id);
      },
      onLane: (ev: LaneEvent) => {
        if (ev.phase === 'progress') {
          setProgress({ done: ev.done ?? 0, total: ev.total ?? totalTables, pct: ev.pct ?? 0 });
          if (ev.made) setMade(ev.made);
          return;
        }
        setLanes((prev) => ({ ...prev, [ev.lane]: { table: ev.table, phase: ev.phase, line: ev.line } }));
        setLogs((prev) => [...prev.slice(-60), ev.line]);
        if (ev.made) {
          setMade((prev) => {
            const next = { ...prev };
            for (const [k, v] of Object.entries(ev.made!)) next[k] = (next[k] ?? 0) + v;
            return next;
          });
        }
      },
      onEnd: (status, m, error) => {
        setPhase('done');
        setEndState({ status, error });
        setMade((prev) => (Object.keys(m).length ? m : prev));
        void qc.invalidateQueries({ queryKey: ['suggestions', datasourceId] });
        if (status === 'done') toast('AI 补全完成，建议已进评审队列', 'ok');
        else if (status === 'cancelled') toast('已取消', 'info');
        else toast(`补全失败：${error ?? '未知错误'}`, 'err');
      },
    });
  };

  const cancel = () => {
    connRef.current?.close();
    if (!mockEnabled() && runId != null) void aiRunsApi.cancelRun(runId);
    setEndState({ status: 'cancelled' });
    setPhase('done');
  };

  const madeText = useMemo(
    () =>
      (Object.entries(made) as [CompletionKind, number][])
        .filter(([, n]) => n > 0)
        .map(([k, n]) => `${KIND_LABEL[k] ?? k} ${n}`)
        .join(' · '),
    [made],
  );

  const laneEntries = Object.entries(lanes).sort(([a], [b]) => Number(a) - Number(b));

  return (
    <Modal
      open={open}
      title={
        phase === 'config'
          ? `AI 补全 · ${scope === 'table' ? tableName : dsName}`
          : phase === 'running'
            ? '补全运行中'
            : '补全完成'
      }
      onClose={onClose}
    >
      {phase === 'config' && (
        <div>
          <div className="form-label">范围</div>
          {scope === 'datasource' ? (
            <>
              <label className="ai-check-row" data-action="范围-整库">
                <input type="radio" name="ai-scope" checked={scopeMode === 'all'} onChange={() => setScopeMode('all')} />
                <span>整库（{totalTables} 张表）</span>
              </label>
              <label className="ai-check-row" data-action="范围-自选表">
                <input type="radio" name="ai-scope" checked={scopeMode === 'pick'} onChange={() => setScopeMode('pick')} />
                <span>自选表{scopeMode === 'pick' && `（已选 ${picked.size} 张）`}</span>
              </label>
              {scopeMode === 'pick' && (
                <div className="ai-table-pick-wrap">
                  <div className="ai-table-pick-bar">
                    <span className="row-act" data-action="全选表" onClick={() => setPicked(new Set(tableNames))}>全选</span>
                    <span className="row-act" data-action="清空表" onClick={() => setPicked(new Set())}>清空</span>
                  </div>
                  <div className="ai-table-pick">
                    {tableNames.map((n) => (
                      <label key={n} className="ai-check-row" data-action={`选择表-${n}`}>
                        <input type="checkbox" checked={picked.has(n)} onChange={() => toggleTable(n)} />
                        <span className="mono">{n}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="ai-scope-note">
              {scope === 'table' && `仅当前表 ${tableName}`}
              {scope === 'relations' && '仅关系推断（AI 生成）'}
            </div>
          )}
          <div className="form-label" style={{ marginTop: 14 }}>内容</div>
          {aiRouteMissing && (
            <div className="ai-scope-note" style={{ color: 'var(--amber)', marginBottom: 8 }}>
              未配置 route.ai：关系推断 / 注释补全 / 标准绑定全部由 AI 生成，需要小模型路由——请到「设置 → 模型路由」配置后再跑。
            </div>
          )}
          {(Object.keys(KIND_LABEL) as CompletionKind[]).map((k) => (
            <label key={k} className="ai-check-row" data-action={`选择补全类型-${k}`}>
              <input
                type="checkbox"
                checked={kinds.includes(k)}
                disabled={(scope === 'relations' && k !== 'relation') || aiRouteMissing}
                onChange={() => toggleKind(k)}
              />
              <span>{KIND_LABEL[k]}</span>
              {k === 'relation' && <span className="muted">（AI 语义推断）</span>}
            </label>
          ))}
          <div className="form-label" style={{ marginTop: 14 }}>选项</div>
          <label className="ai-check-row" data-action="重评已有值">
            <input type="checkbox" checked={rescan} onChange={(e) => setRescan(e.target.checked)} />
            <span>重评已有注释/绑定</span>
          </label>
          <label className="ai-check-row" data-action="使用采样值">
            <input
              type="checkbox"
              checked={useSamples}
              disabled={!online}
              onChange={(e) => setUseSamples(e.target.checked)}
            />
            <span>使用采样值辅助判断</span>
            {!online && <span className="muted">（离线库不可用）</span>}
          </label>
          <div className="form-hint" style={{ margin: '12px 0' }}>
            建议需人工采纳后才生效；AI 只建议、不落库。多泳道并发处理，大库约每张表 1–3 秒。
          </div>
          <div className="modal-foot">
            <Button variant="ghost" data-action="取消补全配置" onClick={onClose}>取消</Button>
            <Button
              variant="accent"
              data-action="开始补全"
              disabled={!kinds.length || (scope === 'datasource' && scopeMode === 'pick' && picked.size === 0)}
              onClick={start}
            >
              <Icon name="spark" size={13} />
              开始补全
            </Button>
          </div>
        </div>
      )}

      {phase !== 'config' && (
        <div>
          <div className={`progress-track${endState?.status === 'failed' ? ' fail' : ''}`} style={{ marginBottom: 12 }}>
            <div
              className="progress-fill"
              style={{ width: `${phase === 'done' ? 100 : progress.pct}%` }}
            />
          </div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
            {phase === 'running'
              ? `进度 ${progress.done}/${progress.total}（${progress.pct}%）· ${madeText || '生产中…'}`
              : endState?.status === 'done'
                ? `完成：${madeText || '无新增建议（均已覆盖或无缺失）'}`
                : endState?.status === 'cancelled'
                  ? '已取消'
                  : `失败：${endState?.error ?? ''}`}
          </div>

          <div className="lane-list">
            {laneEntries.map(([lane, st]) => (
              <div key={lane} className={`lane-row ${st.phase}`}>
                <span className={`lane-dot ${st.phase}`} />
                <span className="lane-name">{`泳道 ${lane}`}</span>
                <span className="lane-table">{st.table}</span>
                <span className="lane-line">{st.line}</span>
              </div>
            ))}
            {phase === 'running' && progress.total - progress.done - laneEntries.filter(([, s]) => s.phase === 'analyzing').length > 0 && (
              <div className="lane-row waiting">
                <span className="lane-dot waiting" />
                <span className="lane-name">排队</span>
                <span className="lane-line">
                  还有 {Math.max(0, progress.total - progress.done - laneEntries.filter(([, s]) => s.phase === 'analyzing').length)} 项待处理
                </span>
              </div>
            )}
          </div>

          {phase === 'running' && (
            <div className="ingest-log" style={{ maxHeight: 160, marginTop: 12 }}>
              {logs.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          )}

          {/* 完成视图：本次产出建议明细（目标 + 建议值 + 依据 + 置信度），先核对再决定评审 */}
          {phase === 'done' && producedItems.length > 0 && (
            <div className="ai-result-list">
              <div className="form-label" style={{ marginBottom: 6 }}>
                本次产出 {producedItems.length} 条（逐条核对内容）：
              </div>
              {producedItems.map((s) => {
                const line = suggestionLine(s);
                return (
                  <div key={s.id} className="ai-result-row">
                    <span className={`sg-kind ${s.kind}`}>{KIND_LABEL[s.kind as CompletionKind] ?? s.kind}</span>
                    <span className="ai-result-target">{line.target}</span>
                    <span className="ai-result-value">{line.value}</span>
                    <span className="sg-conf" style={{ marginLeft: 'auto' }}>
                      {s.confidence.toFixed(2)}
                    </span>
                    {line.reason && <div className="ai-result-reason">依据：{line.reason}</div>}
                  </div>
                );
              })}
            </div>
          )}

          <div className="modal-foot">
            {phase === 'running' ? (
              <Button variant="ghost" data-action="取消补全任务" onClick={cancel}>
                取消
              </Button>
            ) : (
              <>
                <Button variant="ghost" data-action="关闭补全结果" onClick={onClose}>
                  完成
                </Button>
                {canReview && (
                  <Button variant="accent" data-action="评审并采纳" onClick={() => onReview(runId)}>
                    评审并采纳（{producedItems.length || ''}）
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
