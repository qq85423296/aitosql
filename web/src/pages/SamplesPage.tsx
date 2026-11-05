import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { samplesApi } from '../api/samples';
import { datasourcesApi } from '../api/datasources';
import { settingsApi } from '../api/settings';
import type { Sample, SampleStatus } from '../api/types';
import { StatusChip, MiniBadge, DialectBadge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { toast } from '../components/ui/Toast';
import { Icon } from '../components/ui/icons';
import { SampleDrawer } from '../components/samples/SampleDrawer';
import { DsSwitcher } from '../components/query/DsSwitcher';
import { useSessionStore } from '../stores/sessionStore';
import { fmtInt, parseRouteRef } from '../lib/format';

/**
 * SamplesPage（FR-S1~S4）：骨架对齐数据源页——顶栏 + page-head（标题+新增）+ page-sub +
 * 机制说明条 + 工具行（搜索/数据源筛选/状态筛选/计数）+ panel 表格；
 * stale 治理（EXPLAIN 干跑复验回 on）；缓存命中三条件说明（question_norm ∧ status=on ∧ semantic_rev 相等）。
 */
export function SamplesPage() {
  const qc = useQueryClient();
  const { data: samples = [], isLoading } = useQuery({ queryKey: ['samples'], queryFn: () => samplesApi.list() });
  const { data: dsList = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const [kw, setKw] = useState('');
  const [status, setStatus] = useState<'' | SampleStatus>('');
  const [dsFilter, setDsFilter] = useState<number | ''>('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Sample | null>(null);
  const [revalidating, setRevalidating] = useState<number | null>(null);
  const currentDsId = useSessionStore((s) => s.currentDsId);
  const setCurrentDs = useSessionStore((s) => s.setCurrentDs);
  const { data: settingsBundle } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const routeModel = parseRouteRef(settingsBundle?.settings?.['route.generate'] as string)?.model ?? '…';
  const localOnly = settingsBundle?.local_only ?? true;

  const dsMap = useMemo(() => new Map(dsList.map((d) => [d.id, d])), [dsList]);
  const filtered = useMemo(
    () =>
      samples.filter(
        (s) =>
          (!kw || s.question.toLowerCase().includes(kw.toLowerCase())) &&
          (!status || s.status === status) &&
          (!dsFilter || s.datasource_id === dsFilter),
      ),
    [samples, kw, status, dsFilter],
  );
  const onCount = filtered.filter((s) => s.status === 'on').length;
  const staleCount = filtered.filter((s) => s.status === 'stale').length;

  const patchMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<Pick<Sample, 'status'>> }) =>
      samplesApi.patch(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['samples'] }),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => samplesApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['samples'] });
      toast('样例已删除', 'ok');
    },
  });
  const revalMut = useMutation({
    mutationFn: (id: number) => samplesApi.revalidate(id),
    onMutate: (id) => setRevalidating(id),
    onSettled: () => setRevalidating(null),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['samples'] });
      toast('EXPLAIN 干跑通过，已回启用', 'ok');
    },
    onError: (e) => toast(`复验失败：${(e as Error).message}`, 'err'),
  });

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
        <div className="page-inner">
          <div className="page-head">
            <span className="page-title">样例库</span>
            <div className="head-actions">
              <Button variant="accent" data-action="新增样例" onClick={() => { setEditing(null); setDrawerOpen(true); }}>
                <Icon name="plus" size={13} strokeWidth={2.5} />
                新增样例
              </Button>
            </div>
          </div>
          <div className="page-sub">成功查询一键沉淀为"问题 → SQL"样例：相似问题作 few-shot 参考，相同问题命中缓存直返。</div>

          <div className="banner banner-amber" style={{ maxWidth: 'none', margin: '0 0 16px' }}>
            <span>
              样例缓存命中三条件：归一化问题精确匹配 ∧ 状态=启用 ∧ semantic_rev 相等（schema/口径/能力卡任一变更即懒惰失效置过期）
            </span>
          </div>

          <div className="toolbar-row">
            <div className="search-box">
              <Icon name="search" size={14} strokeWidth={2} />
              <input
                placeholder="搜索问题…"
                data-action="搜索样例"
                value={kw}
                onChange={(e) => setKw(e.target.value)}
              />
            </div>
            <select
              className="filter-select"
              data-action="筛选样例数据源"
              value={dsFilter}
              onChange={(e) => setDsFilter(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">全部数据源</option>
              {dsList.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
            <select
              className="filter-select"
              data-action="筛选样例状态"
              value={status}
              onChange={(e) => setStatus(e.target.value as SampleStatus | '')}
            >
              <option value="">全部状态</option>
              <option value="on">启用</option>
              <option value="stale">过期</option>
              <option value="off">停用</option>
            </select>
            <span className="count-note">
              显示 {filtered.length} 条 · 启用 {onCount} · 过期 {staleCount}
            </span>
          </div>

          {isLoading ? (
            <div className="empty-state">加载中…</div>
          ) : filtered.length === 0 ? (
            <div className="empty-state">
              <div className="big">无匹配样例</div>
              <div>样例用于 few-shot 注入与重复问题缓存；试试调整数据源/状态筛选，或清空搜索词</div>
            </div>
          ) : (
            <div className="panel">
              <table className="dt">
                <thead>
                  <tr>
                    <th>问题</th>
                    <th>SQL</th>
                    <th>数据源</th>
                    <th>状态</th>
                    <th style={{ textAlign: 'right' }}>命中</th>
                    <th>最近验证</th>
                    <th>来源</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((s) => {
                    const ds = dsMap.get(s.datasource_id);
                    return (
                      <tr key={s.id} style={s.status === 'stale' ? { opacity: 0.75 } : undefined}>
                        <td style={{ maxWidth: 240, fontWeight: 500 }}>{s.question}</td>
                        <td
                          style={{
                            fontFamily: 'var(--mono)',
                            fontSize: 11.5,
                            maxWidth: 300,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            color: 'var(--t2)',
                          }}
                          title={s.sql}
                        >
                          {s.sql}
                        </td>
                        <td>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-start', whiteSpace: 'nowrap' }}>
                            <span style={{ fontWeight: 500, fontSize: 12.5 }}>{ds?.name ?? `#${s.datasource_id}`}</span>
                            <span>
                              {ds && <DialectBadge dialect={ds.dialect} />}
                              {ds?.mode === 'offline' && (
                                <>
                                  {' '}
                                  <span className="badge-off">离线</span>
                                </>
                              )}
                            </span>
                          </div>
                        </td>
                        <td>
                          <StatusChip status={s.status} />
                          {s.status === 'stale' && s.stale_reason && (
                            <div className="proto-note">原因：{s.stale_reason}</div>
                          )}
                        </td>
                        <td className="num" style={{ textAlign: 'right' }}>{fmtInt(s.hit_count)}</td>
                        <td className="proto-note">
                          {revalidating === s.id
                            ? 'EXPLAIN 干跑验证中…'
                            : s.validated_at
                              ? new Date(s.validated_at).toLocaleString('zh-CN')
                              : '—'}
                        </td>
                        <td><MiniBadge>{s.source ?? 'manual'}</MiniBadge></td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          {s.status === 'stale' && (
                            <Button size="sm" data-action="重新验证样例" disabled={revalidating === s.id} onClick={() => revalMut.mutate(s.id)}>
                              复验
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => { setEditing(s); setDrawerOpen(true); }}>
                            编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            data-action="启用停用样例"
                            onClick={() => patchMut.mutate({ id: s.id, body: { status: s.status === 'off' ? 'on' : 'off' } })}
                          >
                            {s.status === 'off' ? '启用' : '停用'}
                          </Button>
                          <Button size="sm" variant="ghost" data-action="删除样例" onClick={() => delMut.mutate(s.id)}>
                            删除
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      <SampleDrawer open={drawerOpen} sample={editing} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
