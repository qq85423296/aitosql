import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { relationsApi } from '../../api/relations';
import { tablesApi } from '../../api/tables';
import { fmtInt, fmtRel } from '../../lib/format';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import type { AiWorkspaceEntry } from './AiWorkspace';

const SEVEN_DAYS = 7 * 24 * 3600_000;

/**
 * TableList（FR-D5，原型「表」Tab）：search-box + filter-select（全部/近7天有变更/缺注释/已绑标准）
 * + count-note；列 = 表名/注释(行内编辑)/字段/关系(出/入)/行数估算/最近变更/操作；人工注释优先，重摄取不覆盖。
 * AI 统一工作区（2026-11-05 计划 §4.4）：行内「AI 补全」改为打开统一工作区（表入口，
 * 默认 scope=table）；不再各自挂 AiCompleteModal/SuggestionDrawer 双层弹层。
 */
export function TableList({
  datasourceId,
  onOpenTable,
  onOpenAiWorkspace,
}: {
  datasourceId: number;
  onOpenTable: (tableId: number) => void;
  onOpenAiWorkspace: (entry: AiWorkspaceEntry) => void;
}) {
  const qc = useQueryClient();
  const [kw, setKw] = useState('');
  const [filter, setFilter] = useState<'all' | 'changed' | 'nocmt' | 'bound'>('all');
  const [editing, setEditing] = useState<number | null>(null);
  const [editVal, setEditVal] = useState('');

  const { data: tables = [] } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId),
  });
  const { data: relations = [] } = useQuery({
    queryKey: ['relations', datasourceId],
    queryFn: () => relationsApi.list(datasourceId),
  });

  const patchMut = useMutation({
    mutationFn: ({ id, comment }: { id: number; comment: string }) =>
      tablesApi.patch(id, { comment_custom: comment }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['tables', datasourceId] });
      setEditing(null);
      toast('表注释已更新', 'ok');
    },
  });

  const filtered = useMemo(
    () =>
      tables.filter((t) => {
        const q = kw.trim().toLowerCase();
        if (q && !t.name.toLowerCase().includes(q) && !(t.comment || '').toLowerCase().includes(q)) return false;
        if (filter === 'changed' && (!t.updated_at || Date.now() - new Date(t.updated_at).getTime() > SEVEN_DAYS)) return false;
        if (filter === 'nocmt' && t.comment) return false;
        if (filter === 'bound' && !t.bound) return false;
        return true;
      }),
    [tables, kw, filter],
  );

  const relCount = (name: string) => {
    const out = relations.filter((r) => r.from_table === name).length;
    const inn = relations.filter((r) => r.to_table === name).length;
    return `${out} / ${inn}`;
  };

  const startEdit = (t: { id: number; comment: string }) => {
    setEditing(t.id);
    setEditVal(t.comment || '');
  };

  return (
    <div>
      <div className="toolbar-row">
        <div className="search-box">
          <Icon name="search" size={14} strokeWidth={2} />
          <input placeholder="搜索表名或注释…" data-action="搜索表" value={kw} onChange={(e) => setKw(e.target.value)} />
        </div>
        <select className="filter-select" data-action="筛选表" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
          <option value="all">全部</option>
          <option value="changed">近 7 天有变更</option>
          <option value="nocmt">缺注释</option>
          <option value="bound">已绑标准</option>
        </select>
        <span className="count-note">
          {filtered.length} / {tables.length} 张表
        </span>
      </div>
      <div className="panel">
        <table className="dt">
          <thead>
            <tr>
              <th>表名</th>
              <th>注释（点击可编辑）</th>
              <th style={{ textAlign: 'right' }}>字段</th>
              <th style={{ textAlign: 'right' }}>关系(出/入)</th>
              <th style={{ textAlign: 'right' }}>行数估算</th>
              <th>最近变更</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id} className="clickable" data-action="打开表详情" onClick={() => onOpenTable(t.id)}>
                <td>
                  <span className="ds-name" style={{ fontFamily: 'var(--mono)', fontSize: 12.5 }}>
                    {t.schema && <span className="muted" style={{ fontSize: 11 }}>{t.schema}.</span>}
                    {t.name}
                    {!t.comment && <span className="no-comment-dot" title="缺注释"></span>}
                    {t.bound && <span className="tag-mini">标准</span>}
                  </span>
                </td>
                <td
                  className="t-comment-cell"
                  title="点击编辑注释"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (editing !== t.id) startEdit(t);
                  }}
                >
                  {editing === t.id ? (
                    <input
                      className="cmt-input"
                      style={{ width: '100%' }}
                      value={editVal}
                      placeholder="补充表注释，注入提示词"
                      autoFocus
                      onChange={(e) => setEditVal(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        e.stopPropagation();
                        if (e.key === 'Enter') patchMut.mutate({ id: t.id, comment: editVal });
                        if (e.key === 'Escape') setEditing(null);
                      }}
                      onBlur={() => patchMut.mutate({ id: t.id, comment: editVal })}
                    />
                  ) : (
                    t.comment || <span style={{ color: 'var(--amber)', fontSize: 12 }}>缺注释，点击补充</span>
                  )}
                </td>
                <td className="num" style={{ textAlign: 'right' }}>{fmtInt(t.fields)}</td>
                <td className="num" style={{ textAlign: 'right' }}>{relCount(t.name)}</td>
                <td className="num" style={{ textAlign: 'right' }}>{fmtInt(t.rows)}</td>
                <td><span className="muted">{t.updated_at ? fmtRel(t.updated_at) : '—'}</span></td>
                <td>
                  <div className="row-acts">
                    <button className="row-act accent" data-action="打开表详情" onClick={(e) => { e.stopPropagation(); onOpenTable(t.id); }}>
                      详情
                    </button>
                    <button className="row-act" data-action="行内编辑表注释列表" onClick={(e) => { e.stopPropagation(); startEdit(t); }}>
                      注释
                    </button>
                    <button
                      className="row-act"
                      data-action="行内 AI 补全本表"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenAiWorkspace({ datasourceId, tableId: t.id, tableName: t.name, initialView: 'generate' });
                      }}
                    >
                      AI 补全
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length === 0 && <div className="empty-state">无匹配表</div>}
    </div>
  );
}
