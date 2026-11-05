/**
 * AiGenerationPanel —— 统一工作区「生成建议」视图（2026-11-05 计划 §3.2/§5 阶段 2）。
 *
 * - 默认快速：数据源入口默认整库、表入口默认当前表；默认类型 comment/relation/std_bind；样例值默认关；
 *   自选表 / 仅推断关系 / 重扫已有 / 样例值 / 类型选择 全部收进「高级选项」折叠区；
 * - 运行中：本会话 SSE 在 → 实时泳道；连接已丢 → 轮询持久状态 + 「进度可能略有延迟」明示（不伪造泳道）；
 * - 终态：done+0 / failed / cancelled 各自明确，不伪装 100% 进度或成功；历史建议入口始终可达；
 * - route.ai 未配置：原位解释 + 禁用开始 + 前往设置页；活动任务存在时禁新建并解释（409 由控制器兜底）。
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { settingsApi } from '../../api/settings';
import { datasourcesApi } from '../../api/datasources';
import type { CompletionKind } from '../../api/types';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import type { AiRunController } from './useAiCompletionRun';

const KIND_LABEL: Record<CompletionKind, string> = {
  comment: '注释补全',
  relation: '关系推断',
  std_bind: '标准绑定建议',
};
const ALL_KINDS: CompletionKind[] = ['comment', 'relation', 'std_bind'];

export function AiGenerationPanel({
  datasourceId,
  tableId,
  tableName,
  online,
  totalTables,
  ctl,
  onCloseWorkspace,
  onGoReviewAll,
  onReviewBatch,
}: {
  datasourceId: number;
  tableId?: number;
  tableName?: string;
  online: boolean;
  totalTables: number;
  ctl: AiRunController;
  onCloseWorkspace: () => void;
  onGoReviewAll: () => void;
  onReviewBatch: (runId: number) => void;
}) {
  const navigate = useNavigate();
  const isTableEntry = tableId != null;

  // 默认快捷配置（高级选项折叠；默认值对齐产品原则 1）
  const [kinds, setKinds] = useState<CompletionKind[]>(ALL_KINDS);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [scopeMode, setScopeMode] = useState<'all' | 'pick'>('all');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [relationsOnly, setRelationsOnly] = useState(false);
  const [rescan, setRescan] = useState(false);
  const [useSamples, setUseSamples] = useState(false);
  const [cancelConfirm, setCancelConfirm] = useState(false);

  // route.ai 预检：未配置时所有类型均为 AI 生成、无规则快道兜底 → 禁开始（沿用旧模态语义）
  const { data: settingsData } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const aiRouteMissing = settingsData != null && !String(settingsData.settings['route.ai'] ?? '').trim();

  // 自选表清单（仅数据源入口需要）
  const { data: tableRows } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId),
    enabled: !isTableEntry,
  });
  const tableNames = useMemo(() => (tableRows ?? []).map((t) => t.name), [tableRows]);

  const effectiveKinds: CompletionKind[] = relationsOnly ? ['relation'] : kinds;
  const effectiveTotal = isTableEntry ? 1 : scopeMode === 'pick' ? picked.size : totalTables;
  const madeTotal = (m: Record<string, number>) => Object.values(m).reduce((a, b) => a + b, 0);

  const active = ctl.run != null && (ctl.run.status === 'queued' || ctl.run.status === 'running');
  const terminal = ctl.run != null && !active ? ctl.run : null;

  const toggleKind = (k: CompletionKind) =>
    setKinds((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));
  const toggleTable = (name: string) =>
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });

  const start = () => {
    ctl.start({
      datasource_id: datasourceId,
      scope: isTableEntry
        ? { type: 'table', table_id: tableId }
        : relationsOnly
          ? { type: 'relations' }
          : { type: 'datasource', table_names: scopeMode === 'pick' ? [...picked] : undefined },
      kinds: effectiveKinds,
      options: { rescan_existing: rescan, use_sample_values: useSamples },
    });
  };

  // ---------- 运行中（实时 SSE 或 轮询恢复） ----------
  if (active && ctl.run) {
    const r = ctl.run;
    const done = ctl.live?.progress.done ?? r.progress_done;
    const total = ctl.live?.progress.total ?? r.progress_total;
    const pct = ctl.live?.progress.pct ?? (total > 0 ? Math.round((done / total) * 100) : 0);
    const made = ctl.live?.made ?? r.made ?? {};
    const madeText = (Object.entries(made) as [CompletionKind, number][])
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${KIND_LABEL[k] ?? k} ${n}`)
      .join(' · ');
    const laneEntries = ctl.live ? Object.entries(ctl.live.lanes).sort(([a], [b]) => Number(a) - Number(b)) : [];
    return (
      <div>
        <div className="progress-track" style={{ marginTop: 6 }}>
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          {r.status === 'queued' ? '排队等待中…' : `进度 ${done}/${total}（${pct}%）`}
          {ctl.live?.currentTable ? ` · 当前表 ${ctl.live.currentTable}` : ''}
          {madeText ? ` · 已生成 ${madeText}` : ''}
        </div>
        {!ctl.live && (
          <div className="ai-scope-note" style={{ color: 'var(--amber)', marginBottom: 10 }}>
            正在后台分析，进度可能略有延迟（以服务端持久状态为准，非实时事件回放）。
          </div>
        )}
        {laneEntries.length > 0 && (
          <div className="lane-list">
            {laneEntries.map(([lane, st]) => (
              <div key={lane} className={`lane-row ${st.phase}`}>
                <span className={`lane-dot ${st.phase}`} />
                <span className="lane-name">{`泳道 ${lane}`}</span>
                <span className="lane-table">{st.table}</span>
                <span className="lane-line">{st.line}</span>
              </div>
            ))}
          </div>
        )}
        {ctl.live && ctl.live.logs.length > 0 && (
          <div className="ingest-log" style={{ maxHeight: 140, marginTop: 12 }}>
            {ctl.live.logs.map((l, i) => (
              <div key={i}>{l}</div>
            ))}
          </div>
        )}
        <div className="modal-foot" style={{ justifyContent: 'space-between' }}>
          <Button
            variant="danger"
            size="sm"
            data-action="取消分析"
            disabled={ctl.cancelling}
            onClick={() => {
              if (cancelConfirm) {
                setCancelConfirm(false);
                ctl.cancel();
              } else {
                setCancelConfirm(true);
                setTimeout(() => setCancelConfirm(false), 3000);
              }
            }}
          >
            {ctl.cancelling ? '取消中…' : cancelConfirm ? '确认取消分析？' : '取消分析'}
          </Button>
          <Button variant="ghost" size="sm" data-action="收起工作区" onClick={onCloseWorkspace}>
            收起工作区（后台继续分析）
          </Button>
        </div>
      </div>
    );
  }

  // ---------- 终态卡片（不伪装成功；历史建议保持可达） ----------
  if (terminal) {
    const made = terminal.made ?? {};
    const total = madeTotal(made);
    const madeText = (Object.entries(made) as [CompletionKind, number][])
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${KIND_LABEL[k] ?? k} ${n}`)
      .join(' · ');
    return (
      <div>
        {terminal.status === 'done' && total > 0 && (
          <div className="ai-banner" style={{ marginTop: 4 }}>
            <Icon name="check-circle" size={16} />
            完成：{madeText}
            {terminal.failed.length > 0 && <span className="muted">（{terminal.failed.length} 项失败，详见日志）</span>}
          </div>
        )}
        {terminal.status === 'done' && total === 0 && (
          <div className="ai-ws-state">
            <div className="big">本次没有生成新建议</div>
            <div className="muted" style={{ marginTop: 4 }}>现有内容均已覆盖或无缺失；历史待评审建议仍可继续处理。</div>
          </div>
        )}
        {terminal.status === 'failed' && (
          <div className="ai-ws-state">
            <div className="big" style={{ color: 'var(--red)' }}>分析失败</div>
            <div className="fail-detail" style={{ color: 'var(--red)', fontSize: 12, marginTop: 6 }}>
              {terminal.error ?? '未知错误'}
            </div>
          </div>
        )}
        {terminal.status === 'cancelled' && (
          <div className="ai-ws-state">
            <div className="big" style={{ color: 'var(--amber)' }}>已取消</div>
            <div className="muted" style={{ marginTop: 4 }}>任务已被明确取消，未产出完整批次；已生成的部分建议仍在队列中。</div>
          </div>
        )}
        <div className="modal-foot">
          <Button variant="ghost" data-action="终态-查看全部待评审" onClick={onGoReviewAll}>
            查看全部待评审
          </Button>
          {terminal.status === 'done' && total > 0 ? (
            <Button variant="accent" data-action="终态-评审本批建议" onClick={() => onReviewBatch(terminal.id)}>
              评审本批建议（{total}）
            </Button>
          ) : (
            <Button variant="accent" data-action="终态-重新分析" onClick={ctl.resetTerminal}>
              重新分析
            </Button>
          )}
        </div>
      </div>
    );
  }

  // ---------- 配置态（默认快捷 + 折叠高级选项） ----------
  return (
    <div>
      {ctl.startError && (
        <div className="ai-scope-note" style={{ color: 'var(--red)', marginBottom: 10 }}>
          创建任务失败：{ctl.startError}
          <span className="row-act" style={{ marginLeft: 8 }} data-action="清除创建错误" onClick={ctl.clearStartError}>
            知道了
          </span>
        </div>
      )}
      {aiRouteMissing && (
        <div className="ai-scope-note" style={{ color: 'var(--amber)', marginBottom: 10 }}>
          未配置 route.ai：注释补全 / 关系推断 / 标准绑定全部由 AI 生成，需要小模型路由。
          <span className="row-act" style={{ marginLeft: 8 }} data-action="前往设置页" onClick={() => navigate('/settings')}>
            前往设置页
          </span>
        </div>
      )}

      <div className="form-label">范围</div>
      <div className="ai-scope-note">
        {isTableEntry
          ? `仅当前表 ${tableName}`
          : relationsOnly
            ? `整库 ${totalTables} 张表（仅推断关系）`
            : scopeMode === 'all'
              ? `整库 ${totalTables} 张表`
              : `自选 ${picked.size} 张表`}
      </div>

      <div className="form-label" style={{ marginTop: 14 }}>将生成的建议类型</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {effectiveKinds.map((k) => (
          <span key={k} className={`sg-kind ${k}`} style={{ fontSize: 11, padding: '2px 9px' }}>
            {KIND_LABEL[k]}
          </span>
        ))}
      </div>
      <div className="form-hint" style={{ margin: '10px 0' }}>
        建议需人工采纳后才生效；AI 只建议、不落库。预计处理 {effectiveTotal} 张表，大库约每张表 1–3 秒。
      </div>

      <div className="row-act" style={{ display: 'inline-block', marginBottom: 6 }} data-action="展开高级选项" onClick={() => setAdvancedOpen((v) => !v)}>
        高级选项 {advancedOpen ? '▴' : '▾'}
      </div>
      {advancedOpen && (
        <div className="ai-ws-advanced">
          {!isTableEntry && (
            <>
              <div className="form-label">范围细化</div>
              <label className="ai-check-row" data-action="范围-整库">
                <input
                  type="radio"
                  name="ai-ws-scope"
                  checked={scopeMode === 'all'}
                  onChange={() => setScopeMode('all')}
                />
                <span>整库（{totalTables} 张表）</span>
              </label>
              <label className="ai-check-row" data-action="范围-自选表">
                <input
                  type="radio"
                  name="ai-ws-scope"
                  checked={scopeMode === 'pick'}
                  onChange={() => setScopeMode('pick')}
                />
                <span>自选表{scopeMode === 'pick' ? `（已选 ${picked.size} 张）` : ''}</span>
              </label>
              {scopeMode === 'pick' && (
                <div className="ai-table-pick-wrap">
                  <div className="ai-table-pick-bar">
                    <span className="row-act" data-action="全选表" onClick={() => setPicked(new Set(tableNames))}>
                      全选
                    </span>
                    <span className="row-act" data-action="清空表" onClick={() => setPicked(new Set())}>
                      清空
                    </span>
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
              <label className="ai-check-row" data-action="仅推断关系">
                <input type="checkbox" checked={relationsOnly} onChange={(e) => setRelationsOnly(e.target.checked)} />
                <span>仅推断关系</span>
                <span className="muted">（跳过逐表注释/标准泳道，只做关系推断）</span>
              </label>
            </>
          )}
          <div className="form-label" style={{ marginTop: 10 }}>类型选择</div>
          {ALL_KINDS.map((k) => (
            <label key={k} className="ai-check-row" data-action={`选择补全类型-${k}`}>
              <input
                type="checkbox"
                checked={effectiveKinds.includes(k)}
                disabled={relationsOnly}
                onChange={() => toggleKind(k)}
              />
              <span>{KIND_LABEL[k]}</span>
              {k === 'relation' && <span className="muted">（AI 语义推断）</span>}
            </label>
          ))}
          <div className="form-label" style={{ marginTop: 10 }}>选项</div>
          <label className="ai-check-row" data-action="重评已有值">
            <input type="checkbox" checked={rescan} onChange={(e) => setRescan(e.target.checked)} />
            <span>重评已有注释/绑定</span>
          </label>
          <label className="ai-check-row" data-action="使用采样值">
            <input type="checkbox" checked={useSamples} disabled={!online} onChange={(e) => setUseSamples(e.target.checked)} />
            <span>使用采样值辅助判断</span>
            {!online && <span className="muted">（离线库不可用）</span>}
          </label>
        </div>
      )}

      <div className="modal-foot">
        <Button variant="ghost" data-action="关闭工作区" onClick={onCloseWorkspace}>
          关闭
        </Button>
        <Button
          variant="ai"
          data-action="开始分析"
          disabled={
            ctl.creating ||
            aiRouteMissing ||
            effectiveKinds.length === 0 ||
            (!isTableEntry && !relationsOnly && scopeMode === 'pick' && picked.size === 0)
          }
          onClick={start}
        >
          <Icon name="spark" size={13} />
          {ctl.creating ? '创建中…' : '开始分析'}
        </Button>
      </div>
    </div>
  );
}
