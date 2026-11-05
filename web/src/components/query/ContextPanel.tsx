import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { datasourcesApi } from '../../api/datasources';
import { tablesApi } from '../../api/tables';
import { useSessionStore } from '../../stores/sessionStore';
import { useQueryStore } from '../../stores/queryStore';
import { cnName, fmtInt } from '../../lib/format';

/**
 * ContextPanel 320px（FR-Q12）：Schema 树（键徽章/+插入表名/过滤）| 查询详情 两页签。
 * ≤1200px 收拢（app.css 断点）。
 * DSH 对齐 G：底部上下文占用条——Σ 各轮 meta.tokens + 在飞消息本地估算（≈2 字符/token），
 * 超 80% 亮「接近上下文上限，将自动压缩」提示条。
 */

/** 近似上下文窗口大小（token）。契约缺口：后端暂无模型上下文字段，按 32K 估算；后端给出后替换 */
const CTX_LIMIT = 32768;

export function ContextPanel({ datasourceId }: { datasourceId: number | null }) {
  const open = useSessionStore((s) => s.contextPanelOpen);
  const tab = useSessionStore((s) => s.contextTab);
  const setTab = useSessionStore((s) => s.setContextTab);
  const prefillComposer = useSessionStore((s) => s.prefillComposer);
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const messages = useQueryStore((s) => s.messages);
  const lastMsg = messages[messages.length - 1];

  /** G：近似 token 占用 = Σ 各轮 meta.tokens（轮次上下文累积上界）+ 在飞消息字符估算（≈2 字符/token） */
  const usedTokens = useMemo(() => {
    let sum = 0;
    for (const m of messages) sum += m.meta?.tokens ?? 0;
    const last = messages[messages.length - 1];
    if (last && last.status === 'running' && !last.queued) {
      const chars =
        (last.question?.length ?? 0) +
        (last.genPreview?.reasoning.length ?? 0) +
        (last.genPreview?.content.length ?? 0);
      sum += Math.ceil(chars / 2);
    }
    return sum;
  }, [messages]);
  const ctxPct = usedTokens / CTX_LIMIT;

  const { data: tables = [] } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId!),
    enabled: datasourceId != null,
  });

  const filtered = useMemo(() => {
    const kw = filter.trim().toLowerCase();
    return kw ? tables.filter((t) => t.name.toLowerCase().includes(kw)) : tables;
  }, [tables, filter]);

  if (!open) return null;

  return (
    <aside className="ctx-panel" aria-label="上下文面板">
      <div className="ctx-tabs">
        <button
          className={clsx('ctx-tab', tab === 'schema' && 'active')}
          role="tab"
          aria-selected={tab === 'schema'}
          data-action="切换面板页签"
          onClick={() => setTab('schema')}
        >
          Schema 树
        </button>
        <button
          className={clsx('ctx-tab', tab === 'detail' && 'active')}
          role="tab"
          aria-selected={tab === 'detail'}
          data-action="切换面板页签"
          onClick={() => setTab('detail')}
        >
          查询详情
        </button>
      </div>
      <div className="ctx-body">
        {tab === 'schema' ? (
          <>
            <input
              className="form-input"
              placeholder="过滤表名…"
              data-action="过滤Schema树"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            {filtered.map((t) => (
              <div className="schema-table" key={t.id}>
                <div
                  className="schema-table-hd"
                  data-action="展开表字段"
                  aria-expanded={!!expanded[t.id]}
                  onClick={() => setExpanded((p) => ({ ...p, [t.id]: !p[t.id] }))}
                >
                  <span aria-hidden>{expanded[t.id] ? '▾' : '▸'}</span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    {t.name}
                    {cnName(t.comment) && <span className="cn-tag">（{cnName(t.comment)}）</span>}
                  </span>
                  {t.partition && <span className="key-badge">分区·{t.partition}</span>}
                  <button
                    className="icon-btn"
                    title="插入表名"
                    data-action="插入表名"
                    onClick={(e) => {
                      e.stopPropagation();
                      prefillComposer(`${t.name}`);
                    }}
                  >
                    ＋
                  </button>
                </div>
                {expanded[t.id] && <TableColumns tableId={t.id} />}
              </div>
            ))}
            {filtered.length === 0 && <div className="proto-note">无匹配表</div>}
          </>
        ) : (
          <div style={{ fontSize: 12.5, color: 'var(--t2)' }}>
            {lastMsg ? (
              <>
                <div style={{ marginBottom: 8 }}>
                  <strong>当前查询</strong>：{lastMsg.question}
                </div>
                <div>状态：{lastMsg.status}</div>
                {lastMsg.meta && (
                  <>
                    <div>命中表：{lastMsg.meta.hit_tables.join('、') || '—'}</div>
                    <div>tokens：{fmtInt(lastMsg.meta.tokens)}</div>
                  </>
                )}
                {lastMsg.resultMeta && (
                  <>
                    <div>行数：{fmtInt(lastMsg.resultMeta.rows)}</div>
                    <div>扫描：{fmtInt(lastMsg.resultMeta.scan_rows)}</div>
                    <div>数据时间：{lastMsg.resultMeta.data_max_time ?? '—'}</div>
                  </>
                )}
              </>
            ) : (
              <span className="proto-note">本会话暂无查询</span>
            )}
          </div>
        )}
      </div>
      {/* G：上下文占用指示（meta.tokens 累计 + 本地估算；≥80% 亮自动压缩提示条） */}
      <div className={ctxPct >= 0.8 ? 'ctx-usage warn' : 'ctx-usage'}>
        <div className="ctx-usage-row">
          <span>上下文占用</span>
          <span className="ctx-usage-val">
            ~{fmtInt(usedTokens)} / ~{fmtInt(CTX_LIMIT)}（{Math.round(ctxPct * 100)}%）
          </span>
        </div>
        <div className={clsx('capacity-bar', ctxPct >= 0.8 && 'warn')} style={{ marginTop: 4 }}>
          <div style={{ width: `${Math.min(100, Math.round(ctxPct * 100))}%` }} />
        </div>
        {ctxPct >= 0.8 && <div className="ctx-usage-warn">接近上下文上限，将自动压缩</div>}
      </div>
    </aside>
  );
}

function TableColumns({ tableId }: { tableId: number }) {
  // P1-5：后端无 /tables/:id/columns 路由——字段列取自 GET /tables/:id 的 {table, columns} 包络
  const { data } = useQuery({
    queryKey: ['table', tableId],
    queryFn: () => tablesApi.get(tableId),
  });
  const cols = data?.columns ?? [];
  return (
    <div className="schema-cols">
      {cols.map((c) => (
        <div className="col" key={c.id} title={c.comment_custom ?? c.comment_db ?? ''}>
          <span style={{ flex: 1, minWidth: 0 }}>
            {c.name}
            {cnName(c.comment_custom ?? c.comment_db) && (
              <span className="cn-tag">（{cnName(c.comment_custom ?? c.comment_db)}）</span>
            )}
          </span>
          {!!c.is_pk && <span className="key-badge pk">PK</span>}
          {!!c.is_partition && <span className="key-badge">分区</span>}
          {!!c.is_bucket && <span className="key-badge bucket">分桶</span>}
          <span style={{ color: 'var(--t3)' }}>{c.type}</span>
        </div>
      ))}
    </div>
  );
}
