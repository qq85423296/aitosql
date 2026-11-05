import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi, type CreateDatasourceReq } from '../../api/datasources';
import { connectIngestStream } from '../../api/sse';
import { mockEnabled } from '../../api/client';
import type { Datasource } from '../../api/types';
import { DialectBadge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Drawer } from '../ui/Drawer';
import { Modal } from '../ui/Modal';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import { fmtInt, fmtRel } from '../../lib/format';
import { InstructionDrawer } from './InstructionDrawer';

/**
 * DsList（FR-D1，原型视图 1）：数据源表格——名称/方言/接入/主机/版本/表数/上次摄取/状态/行操作。
 * 行操作：详情 / 重新摄取（离线=导入）/ 口径说明（契约 5）/ 编辑 / 删除；点行进详情。
 */
export function DsList({
  onOpenDetail,
  onOpenWizard,
}: {
  onOpenDetail: (id: number) => void;
  onOpenWizard: () => void;
}) {
  const qc = useQueryClient();
  const { data: list = [], isLoading } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const [editing, setEditing] = useState<Datasource | null>(null);
  const [removing, setRemoving] = useState<Datasource | null>(null);
  const [importing, setImporting] = useState<Datasource | null>(null);
  const [instructionDs, setInstructionDs] = useState<Datasource | null>(null);

  const removeMut = useMutation({
    mutationFn: (id: number) => datasourcesApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['datasources'] });
      setRemoving(null);
      toast('数据源已删除', 'ok');
    },
    onError: (e) => toast(`删除失败：${(e as Error).message}`, 'err'),
  });

  /** 重新摄取：SSE 进度；mock 下模拟 */
  const reingest = (d: Datasource) => reingestDs(d, qc);

  return (
    <>
      <div className="page-head">
        <span className="page-title">数据源</span>
        <div className="head-actions">
          <Button variant="accent" data-action="打开注册向导" onClick={onOpenWizard}>
            <Icon name="plus" size={13} strokeWidth={2.5} />
            添加数据源
          </Button>
        </div>
      </div>
      <div className="page-sub">在线直连或离线配置均可。点名称进入详情，管理表、字段、关系、标准与指标口径。</div>

      {isLoading ? (
        <div className="empty-state">加载中…</div>
      ) : list.length === 0 ? (
        <div className="empty-state">
          <div className="big">还没有数据源</div>
          <div>支持在线连接（MySQL/Doris/ClickHouse）或离线 DDL 导入</div>
          <Button variant="accent" style={{ marginTop: 12 }} data-action="打开注册向导" onClick={onOpenWizard}>
            注册第一个数据源
          </Button>
        </div>
      ) : (
        <div className="panel">
          <table className="dt">
            <thead>
              <tr>
                <th>名称</th>
                <th>方言</th>
                <th>接入</th>
                <th>主机</th>
                <th>版本</th>
                <th style={{ textAlign: 'right' }}>表数</th>
                <th>上次摄取</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list.map((d) => (
                <tr key={d.id} className="clickable" data-action="打开数据源详情" onClick={() => onOpenDetail(d.id)}>
                  <td>
                    <span className="link">{d.name}</span>
                  </td>
                  <td>
                    <DialectBadge dialect={d.dialect} />
                  </td>
                  <td>{d.mode === 'online' ? <span className="muted">在线</span> : <span className="badge-off">离线</span>}</td>
                  <td>
                    {d.mode === 'online' ? (
                      <span className="host">
                        {d.host ?? ''}
                        {d.port ? `:${d.port}` : ''}
                      </span>
                    ) : (
                      <span className="muted">（不允许直连）</span>
                    )}
                  </td>
                  <td>{d.version ? <span className="ver ver-ok">{d.version}</span> : <span className="muted">—</span>}</td>
                  <td className="num" style={{ textAlign: 'right' }}>{fmtInt(d.table_count)}</td>
                  <td>
                    <span className="muted">
                      {d.mode === 'offline' && d.last_ingest_at ? 'DDL 导入 · ' : ''}
                      {fmtRel(d.last_ingest_at)}
                    </span>
                  </td>
                  <td>
                    {d.mode === 'offline' ? (
                      <span className="status-dot off">离线 · 仅生成</span>
                    ) : d.dialect === 'clickhouse' ? (
                      <span className="status-dot ok">正常 · 只读</span>
                    ) : (
                      <span className="status-dot ok">正常</span>
                    )}
                  </td>
                  <td>
                    <div className="row-acts">
                      <button
                        className="row-act accent"
                        data-action="打开数据源详情"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenDetail(d.id);
                        }}
                      >
                        详情
                      </button>
                      {d.mode === 'online' ? (
                        <button
                          className="row-act"
                          data-action="重新摄取"
                          onClick={(e) => {
                            e.stopPropagation();
                            reingest(d);
                          }}
                        >
                          重新摄取
                        </button>
                      ) : (
                        <button
                          className="row-act"
                          data-action="导入DDL"
                          onClick={(e) => {
                            e.stopPropagation();
                            setImporting(d);
                          }}
                        >
                          导入
                        </button>
                      )}
                      <button
                        className="row-act"
                        data-action="口径说明"
                        title="编辑注入生成提示词的数据源口径段"
                        onClick={(e) => {
                          e.stopPropagation();
                          setInstructionDs(d);
                        }}
                      >
                        口径
                      </button>
                      <button
                        className="row-act"
                        data-action="编辑数据源"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing(d);
                        }}
                      >
                        编辑
                      </button>
                      <button
                        className="row-act danger"
                        data-action="删除数据源"
                        onClick={(e) => {
                          e.stopPropagation();
                          setRemoving(d);
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 编辑抽屉 */}
      <EditDsDrawer ds={editing} onClose={() => setEditing(null)} />
      {/* 删除确认 */}
      <Modal
        open={removing != null}
        title={`删除数据源「${removing?.name ?? ''}」`}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              取消
            </Button>
            <Button variant="danger" data-action="确认删除数据源" disabled={removeMut.isPending} onClick={() => removing && removeMut.mutate(removing.id)}>
              确认删除
            </Button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: 'var(--t2)', lineHeight: 1.7 }}>
          将删除该数据源的表 / 字段 / 关系 / 建议等全部元数据（样例库引用同步失效）。
          {removing?.mode === 'online' ? '不会触碰远端真实库。' : '离线 DDL 导入的元数据一并删除。'}
        </div>
      </Modal>
      {/* 离线 DDL 导入抽屉 */}
      <ImportDdlDrawer ds={importing} onClose={() => setImporting(null)} />
      {/* 契约 5：口径说明编辑抽屉（注入生成提示词的数据源口径段） */}
      <InstructionDrawer ds={instructionDs} onClose={() => setInstructionDs(null)} />
    </>
  );
}

/** 重新摄取（共享）：SSE 进度推送；mock 下模拟一次完整摄取 */
export function reingestDs(d: Datasource, qc: ReturnType<typeof useQueryClient>) {
  toast(`开始重新摄取「${d.name}」…`);
  if (mockEnabled()) {
    setTimeout(() => {
      void qc.invalidateQueries({ queryKey: ['datasources'] });
      void qc.invalidateQueries({ queryKey: ['tables', d.id] });
      toast(`「${d.name}」摄取完成：${d.table_count ?? 0} 表`, 'ok');
    }, 1200);
    return;
  }
  connectIngestStream(d.id, {
    onProgress: () => {},
    onDone: (err) => {
      void qc.invalidateQueries({ queryKey: ['datasources'] });
      void qc.invalidateQueries({ queryKey: ['tables', d.id] });
      if (err) toast(`摄取中断：${err}`, 'err');
      else toast(`「${d.name}」摄取完成`, 'ok');
    },
  });
}

/** 编辑数据源抽屉：名称/方言/主机/端口/库名/账号（密码不回显，留空=不变） */
export function EditDsDrawer({ ds, onClose }: { ds: Datasource | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Partial<CreateDatasourceReq>>({});
  const saveMut = useMutation({
    mutationFn: () => datasourcesApi.patch(ds!.id, { ...form, password: form.password || undefined }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['datasources'] });
      onClose();
      toast('数据源已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });

  // 打开时初始化表单
  if (ds && form.name == null) {
    setForm({
      name: ds.name,
      dialect: ds.dialect,
      mode: ds.mode,
      host: ds.host ?? '',
      port: ds.port ?? undefined,
      db_name: ds.db_name ?? '',
      username: ds.username ?? '',
    });
  }

  return (
    <Drawer
      open={ds != null}
      title={`编辑数据源 · ${ds?.name ?? ''}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="accent" data-action="保存数据源" disabled={saveMut.isPending || !form.name} onClick={() => saveMut.mutate()}>
            保存
          </Button>
        </>
      }
    >
      {ds && (
        <>
          <label className="form-label">名称 *</label>
          <input className="form-input" value={form.name ?? ''} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          <label className="form-label" style={{ marginTop: 10 }}>方言</label>
          <select
            className="form-select"
            value={form.dialect}
            onChange={(e) => setForm((f) => ({ ...f, dialect: e.target.value as CreateDatasourceReq['dialect'] }))}
          >
            <option value="mysql">MySQL</option>
            <option value="doris">Doris</option>
            <option value="clickhouse">ClickHouse</option>
          </select>
          {ds.mode === 'online' && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginTop: 10 }}>
                <div>
                  <label className="form-label">主机</label>
                  <input className="form-input mono" value={form.host ?? ''} onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))} />
                </div>
                <div>
                  <label className="form-label">端口</label>
                  <input
                    className="form-input mono"
                    value={form.port ?? ''}
                    onChange={(e) => setForm((f) => ({ ...f, port: e.target.value ? Number(e.target.value) : undefined }))}
                  />
                </div>
              </div>
              <label className="form-label" style={{ marginTop: 10 }}>数据库名</label>
              <input className="form-input mono" value={form.db_name ?? ''} onChange={(e) => setForm((f) => ({ ...f, db_name: e.target.value }))} />
              <label className="form-label" style={{ marginTop: 10 }}>用户名</label>
              <input className="form-input mono" value={form.username ?? ''} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
              <label className="form-label" style={{ marginTop: 10 }}>密码（留空 = 不修改）</label>
              <input
                className="form-input mono"
                type="password"
                value={form.password ?? ''}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
              />
            </>
          )}
          {ds.mode === 'offline' && (
            <div className="form-hint" style={{ marginTop: 10 }}>
              离线库无连接信息；元数据通过「导入」通道维护（DDL / 模板 / AI 录入）。
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}

/** 离线库 DDL 导入抽屉：粘贴 → 解析预览（失败行标红）→ 入库 */
function ImportDdlDrawer({ ds, onClose }: { ds: Datasource | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [ddl, setDdl] = useState('');
  const [preview, setPreview] = useState<{ tables: { name: string; comment?: string; columns: { name: string; type: string }[] }[]; errors: { line: number; message: string }[] } | null>(null);
  const [committing, setCommitting] = useState(false);

  const parse = async () => {
    if (!ds) return;
    try {
      const r = await datasourcesApi.importDdlPreview(ds.id, ddl);
      setPreview(r);
    } catch (e) {
      toast(`DDL 解析失败：${(e as Error).message}`, 'err');
    }
  };
  const commit = async () => {
    if (!ds) return;
    setCommitting(true);
    try {
      const r = await datasourcesApi.importDdlCommit(ds.id, ddl);
      toast(`DDL 已入库：导入 ${r.imported} 张表`, 'ok');
      void qc.invalidateQueries({ queryKey: ['datasources'] });
      void qc.invalidateQueries({ queryKey: ['tables', ds.id] });
      onClose();
    } catch (e) {
      toast(`DDL 入库失败：${(e as Error).message}`, 'err');
    } finally {
      setCommitting(false);
    }
  };

  return (
    <Drawer
      open={ds != null}
      wide
      title={`导入元数据 · ${ds?.name ?? ''}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button variant="accent" data-action="DDL入库" disabled={!preview || committing} onClick={() => void commit()}>
            {committing ? '入库中…' : '入库'}
          </Button>
        </>
      }
    >
      <label className="form-label">粘贴 DDL（支持多条 CREATE TABLE）</label>
      <textarea className="ddl-area" value={ddl} onChange={(e) => setDdl(e.target.value)} placeholder={'CREATE TABLE orders (\n  id BIGINT COMMENT \'订单ID\',\n  …\n) COMMENT=\'订单主表\';'} />
      <div style={{ marginTop: 10 }}>
        <Button size="sm" data-action="解析DDL" disabled={!ddl.trim()} onClick={() => void parse()}>
          <Icon name="code" size={13} />
          解析 DDL
        </Button>
      </div>
      {preview && (
        <div className="parsed-preview">
          <div className="parsed-summary">
            <Icon name="check-circle" size={14} />
            解析完成：成功 <b>{preview.tables.length}</b> 张表
            {preview.errors.length > 0 && (
              <>
                {' '}/ 失败 <b style={{ color: 'var(--red)' }}>{preview.errors.length}</b> 处 · 成功部分可直接入库
              </>
            )}
          </div>
          <div className="panel">
            <table className="dt">
              <thead>
                <tr><th>表</th><th>字段</th><th>类型</th></tr>
              </thead>
              <tbody>
                {preview.tables.flatMap((t) =>
                  t.columns.map((c, i) => (
                    <tr key={`${t.name}-${i}`}>
                      <td className="mono-cell" style={{ color: 'var(--t1)' }}>{i === 0 ? `${t.name}${t.comment ? ` · ${t.comment}` : ''}` : ''}</td>
                      <td className="mono-cell">{c.name}</td>
                      <td className="mono-cell">{c.type}</td>
                    </tr>
                  )),
                )}
                {preview.errors.map((er, i) => (
                  <tr key={`err-${i}`} className="row-fail">
                    <td colSpan={3}>第 {er.line} 行：{er.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Drawer>
  );
}
