import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { tablesApi } from '../../api/tables';
import { relationsApi } from '../../api/relations';
import { standardsApi, dimensionsApi } from '../../api/standards';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import { fmtInt, fmtRel } from '../../lib/format';
import type { AiWorkspaceEntry } from './AiWorkspace';
import type { ColumnMeta, TableListItem, TableMeta } from '../../api/types';

/**
 * TableDetail（FR-D6/D7/D8/D9，原型视图 4）：面包屑 / td-stats（主键·分区·分桶）/
 * 字段表（注释行内编辑 + 标准绑定下拉 + 维度 chip）/ 双语关系（conf-bar + 手动添加行）/
 * 提示词视图切换 / 抽屉 ×2（字段编辑 · 表信息）。
 * AI 统一工作区（2026-11-05 计划 §4.4）：仅保留「AI 完善本表」入口（scope=table），
 * 不再挂 AiCompleteModal/SuggestionDrawer 双层弹层；工作区评审视图默认按当前表过滤。
 */
export function TableDetail({
  table,
  columns,
  datasourceId,
  dsName,
  onBack,
  onBackToList,
  onOpenAiWorkspace,
}: {
  table: TableMeta;
  /** GET /tables/:id 包络内字段列（P1-5：后端无独立 columns 路由） */
  columns: (ColumnMeta & { standard_name?: string | null })[];
  datasourceId: number;
  dsName: string;
  onBack: () => void;
  onBackToList: () => void;
  onOpenAiWorkspace: (entry: AiWorkspaceEntry) => void;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<'columns' | 'relations'>('columns');
  const [promptView, setPromptView] = useState<string | null>(null);
  const [editCol, setEditCol] = useState<ColumnMeta | null>(null);
  const [editTable, setEditTable] = useState(false);
  const [cmtEditing, setCmtEditing] = useState(false);
  const [cmtVal, setCmtVal] = useState('');

  const cols = columns;
  const { data: rels = [] } = useQuery({
    queryKey: ['relations', datasourceId],
    queryFn: () => relationsApi.list(datasourceId),
  });
  const { data: tables = [] } = useQuery({
    queryKey: ['tables', datasourceId],
    queryFn: () => datasourcesApi.tables(datasourceId),
  });
  const { data: standards = [] } = useQuery({ queryKey: ['standards'], queryFn: standardsApi.list });
  const { data: dims = [] } = useQuery({ queryKey: ['dimensions'], queryFn: dimensionsApi.list });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['table', table.id] });
    void qc.invalidateQueries({ queryKey: ['tables', datasourceId] });
  };
  const patchCol = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof tablesApi.patchColumn>[1] }) =>
      tablesApi.patchColumn(id, body),
    onSuccess: () => {
      invalidate();
      setEditCol(null);
      toast('字段已保存', 'ok');
    },
  });
  const patchTable = useMutation({
    mutationFn: (body: Parameters<typeof tablesApi.patch>[1]) => tablesApi.patch(table.id, body),
    onSuccess: () => {
      invalidate();
      setEditTable(false);
      setCmtEditing(false);
      toast('表信息已保存', 'ok');
    },
  });
  const confirmRel = useMutation({
    mutationFn: (id: number) => relationsApi.confirm(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['relations', datasourceId] });
      toast('关系已确认，将作为 join 依据注入提示词', 'ok');
    },
  });
  const delRel = useMutation({
    mutationFn: (id: number) => relationsApi.remove(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['relations', datasourceId] }),
  });

  const togglePromptView = async () => {
    if (promptView != null) {
      setPromptView(null);
      return;
    }
    const r = await tablesApi.promptView(table.id);
    setPromptView(r.text); // 与真实注入逐字一致（D-09 同源）
  };

  const tableRels = rels.filter((r) => r.from_table === table.name || r.to_table === table.name);
  const outCnt = rels.filter((r) => r.from_table === table.name).length;
  const inCnt = rels.filter((r) => r.to_table === table.name).length;
  const pkCols = cols.filter((c) => c.is_pk).map((c) => c.name);
  const partCols = cols.filter((c) => c.is_partition).map((c) => c.name);
  const bucketCols = cols.filter((c) => c.is_bucket).map((c) => c.name);
  const comment = table.comment_custom ?? table.comment_db ?? '';
  const partLabel = table.partition_type === 'month' ? '月' : table.partition_type === 'day' ? '日' : '';

  /** 中文名对照（双语关系）：表名 → 注释 */
  const cnOf = useMemo(() => {
    const m = new Map<string, string>();
    tables.forEach((t) => m.set(t.name, t.comment || '缺注释'));
    return (name: string) => m.get(name) ?? '';
  }, [tables]);

  const dimOfStd = (stdId?: number | null) => dims.find((d) => d.id === standards.find((s) => s.id === stdId)?.dimension_standard_id);

  return (
    <div>
      <div className="crumb">
        <a data-action="返回数据源列表" onClick={onBackToList}>数据源</a>
        <span>/</span>
        <a data-action="返回数据源详情" onClick={onBack}>{dsName}</a>
        <span>/</span>
        <span style={{ color: 'var(--t1)', fontFamily: 'var(--mono)' }}>{table.name}</span>
      </div>
      <div className="td-head">
        <span className="td-name">{table.name}</span>
        <div className="head-actions">
          <Button
            variant="ai"
            size="sm"
            data-action="AI 完善本表"
            onClick={() => onOpenAiWorkspace({ datasourceId, tableId: table.id, tableName: table.name, initialView: 'generate' })}
          >
            <Icon name="spark" size={13} />
            AI 完善本表
          </Button>
          <button className="pv-toggle" data-action="编辑表信息" onClick={() => setEditTable(true)}>
            <Icon name="pencil" size={14} />
            编辑表信息
          </button>
          <button className={`pv-toggle${promptView != null ? ' on' : ''}`} data-action="提示词视图" onClick={() => void togglePromptView()}>
            <Icon name="eye" size={14} />
            提示词视图
          </button>
        </div>
      </div>
      <div className="cmt-edit">
        <Icon name="pencil" size={12} />
        {cmtEditing ? (
          <input
            className="cmt-input"
            value={cmtVal}
            autoFocus
            onChange={(e) => setCmtVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') patchTable.mutate({ comment_custom: cmtVal });
              if (e.key === 'Escape') setCmtEditing(false);
            }}
            onBlur={() => patchTable.mutate({ comment_custom: cmtVal })}
          />
        ) : (
          <span
            className="cmt-text"
            title="点击编辑人工注释"
            data-action="行内编辑表注释"
            onClick={() => {
              setCmtVal(table.comment_custom ?? table.comment_db ?? '');
              setCmtEditing(true);
            }}
          >
            {comment || '（无注释，点击补充）'}
            {table.comment_custom ? '（人工注释）' : ''}
          </span>
        )}
      </div>
      <div className="td-stats">
        <span><b>{cols.length}</b> 字段</span>
        <span className="stat-sep"></span>
        <span><b>{fmtInt(table.row_estimate)}</b> 行（估算）</span>
        <span className="stat-sep"></span>
        <span>关系 <b>{outCnt} 出 / {inCnt} 入</b></span>
        <span className="stat-sep"></span>
        <span>最近变更 {fmtRel(table.updated_at)}</span>
        {pkCols.length > 0 && (
          <>
            <span className="stat-sep"></span>
            <span className="key-stat" title="主键">
              <Icon name="key" size={11} />
              主键 <b>{pkCols.join(' + ')}</b>
            </span>
          </>
        )}
        {partCols.length > 0 && (
          <>
            <span className="stat-sep"></span>
            <span className="key-stat part" title="分区键：WHERE 含此列可避免全分区扫描">
              <Icon name="grid" size={11} />
              分区 <b>{partCols.join(' + ')}{partLabel ? `（${partLabel}）` : ''}</b>
            </span>
          </>
        )}
        {bucketCols.length > 0 && (
          <>
            <span className="stat-sep"></span>
            <span className="key-stat bucket" title="Doris 分桶键">
              分桶 <b>{bucketCols.join(' + ')}</b>
            </span>
          </>
        )}
      </div>

      <div className="tabbar">
        <div role="tab" aria-selected={tab === 'columns'} className={`tab-item${tab === 'columns' ? ' active' : ''}`} data-action="表详情页签切换" onClick={() => setTab('columns')}>
          字段<span className="cnt">{cols.length}</span>
        </div>
        <div role="tab" aria-selected={tab === 'relations'} className={`tab-item${tab === 'relations' ? ' active' : ''}`} data-action="表详情页签切换" onClick={() => setTab('relations')}>
          关联关系<span className="cnt">{tableRels.length}</span>
        </div>
      </div>

      {promptView != null ? (
        <pre className="prompt-preview">{promptView}</pre>
      ) : tab === 'columns' ? (
        <div className="panel">
          <table className="dt">
            <thead>
              <tr>
                <th>字段名</th>
                <th>类型</th>
                <th>注释（点击可编辑）</th>
                <th>字段标准</th>
                <th>维度标准</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {cols.map((c) => (
                <ColRow
                  key={c.id}
                  col={c}
                  dimName={dimOfStd(c.field_standard_id)?.name}
                  standards={standards}
                  onBind={(stdId) => patchCol.mutate({ id: c.id, body: { field_standard_id: stdId } })}
                  onEditComment={(v) => patchCol.mutate({ id: c.id, body: { comment_custom: v } })}
                  onOpenDrawer={() => setEditCol(c)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="panel">
          <table className="dt">
            <thead>
              <tr>
                <th>关系（英文 + 中文对照）</th>
                <th>来源</th>
                <th>置信度</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tableRels.map((r) => {
                const isIn = r.to_table === table.name;
                return (
                  <tr key={r.id}>
                    <td>
                      <span className="mono-cell" style={{ color: 'var(--t1)' }}>
                        {r.from_table}
                      </span>
                      <span className="rel-cn">（{cnOf(r.from_table)}）</span>
                      <span className="mono-cell">.{r.from_col}</span>
                      <span className="rel-cn"> → </span>
                      <span className="mono-cell" style={{ color: 'var(--t1)' }}>
                        {r.to_table}
                      </span>
                      <span className="rel-cn">（{cnOf(r.to_table)}）</span>
                      <span className="mono-cell">.{r.to_col}</span>
                      {isIn && <span className="tag-mini">引入</span>}
                    </td>
                    <td>
                      <span className={`rel-chip ${r.source}`}>{r.source === 'fk' ? '外键' : r.source === 'inferred' ? '推测' : '手动'}</span>
                    </td>
                    <td>
                      <span className="conf-bar">
                        <span className="conf-track">
                          <span className={`conf-fill${r.status === 'confirmed' ? ' ok' : ''}`} style={{ width: `${r.confidence * 100}%` }}></span>
                        </span>
                        <span className="num">{Math.round(r.confidence * 100)}%</span>
                      </span>
                    </td>
                    <td>
                      {r.status === 'pending' ? (
                        <span className="muted" style={{ color: 'var(--amber)' }}>待确认</span>
                      ) : (
                        <span className="muted" style={{ color: 'var(--green)' }}>已确认</span>
                      )}
                    </td>
                    <td>
                      <div className="row-acts">
                        {r.status === 'pending' && (
                          <button className="row-act confirm" data-action="关系确认" onClick={() => confirmRel.mutate(r.id)}>
                            确认
                          </button>
                        )}
                        <button className="row-act danger" data-action="关系删除" onClick={() => delRel.mutate(r.id)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <AddRelRow datasourceId={datasourceId} tables={tables} current={table.name} />
        </div>
      )}

      {/* 抽屉：字段编辑（在线库字段名/类型只读） */}
      <FieldDrawer
        col={editCol}
        tableName={table.name}
        standards={standards}
        dims={dims}
        onClose={() => setEditCol(null)}
        onSave={(body) => editCol && patchCol.mutate({ id: editCol.id, body })}
      />

      {/* 抽屉：表信息 */}
      <TableInfoDrawer
        open={editTable}
        table={table}
        onClose={() => setEditTable(false)}
        onSave={(body) => patchTable.mutate(body)}
      />
    </div>
  );
}

/** 字段行：注释行内编辑 + 标准绑定下拉 + 维度 chip */
function ColRow({
  col,
  dimName,
  standards,
  onBind,
  onEditComment,
  onOpenDrawer,
}: {
  col: ColumnMeta & { standard_name?: string | null };
  dimName?: string;
  standards: { id: number; name: string }[];
  onBind: (stdId: number | null) => void;
  onEditComment: (v: string) => void;
  onOpenDrawer: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState('');
  const comment = col.comment_custom ?? col.comment_db ?? '';
  return (
    <tr>
      <td>
        <span className="mono-cell" style={{ color: 'var(--t1)', fontWeight: 600 }}>
          {col.name}
        </span>
        {!!col.is_pk && (
          <span title="主键">
            <Icon name="key" size={11} className="pk-icon" style={{ marginLeft: 4 }} />
          </span>
        )}
        {!!col.is_partition && <span className="tag-part" title="分区键：WHERE 含此列可分区裁剪">分区</span>}
        {!!col.is_bucket && <span className="tag-bucket" title="分桶键">分桶</span>}
      </td>
      <td>
        <span className="mono-cell">{col.type}</span>
      </td>
      <td
        className="cmt-cell"
        title="点击编辑"
        data-action="行内编辑字段注释"
        onClick={() => {
          if (!editing) {
            setVal(comment);
            setEditing(true);
          }
        }}
      >
        {editing ? (
          <input
            value={val}
            autoFocus
            onChange={(e) => setVal(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                onEditComment(val);
                setEditing(false);
              }
              if (e.key === 'Escape') setEditing(false);
            }}
            onBlur={() => {
              onEditComment(val);
              setEditing(false);
            }}
          />
        ) : (
          comment || <span style={{ color: 'var(--amber)', fontSize: 12 }}>缺注释</span>
        )}
      </td>
      <td>
        <select
          className={`col-std-select${col.field_standard_id ? ' bound' : ''}`}
          data-action="绑定字段标准"
          value={col.field_standard_id ?? ''}
          onChange={(e) => {
            onBind(e.target.value ? Number(e.target.value) : null);
            toast(e.target.value ? `已绑定标准「${standards.find((s) => s.id === Number(e.target.value))?.name ?? ''}」` : '已解除绑定', 'ok');
          }}
        >
          <option value="">— 未绑定 —</option>
          {standards.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </td>
      <td>{dimName ? <span className="dim-chip">{dimName}</span> : <span className="muted">—</span>}</td>
      <td>
        <button className="row-act" data-action="打开字段抽屉" onClick={onOpenDrawer}>
          编辑
        </button>
      </td>
    </tr>
  );
}

/** 字段编辑抽屉（原型 fdDrawer）：名/类型只读 + 键属性三开关 + 人工注释 + 标准绑定（维度随动） */
function FieldDrawer({
  col,
  tableName,
  standards,
  dims,
  onClose,
  onSave,
}: {
  col: ColumnMeta | null;
  tableName: string;
  standards: { id: number; name: string; dimension_standard_id?: number | null }[];
  dims: { id: number; name: string }[];
  onClose: () => void;
  onSave: (body: Parameters<typeof tablesApi.patchColumn>[1]) => void;
}) {
  const [cmt, setCmt] = useState('');
  const [pk, setPk] = useState(false);
  const [part, setPart] = useState(false);
  const [bucket, setBucket] = useState(false);
  const [stdId, setStdId] = useState<number | ''>('');

  // 打开时同步初值
  if (col && cmt === '' && !pk && !part && !bucket && stdId === '') {
    setCmt(col.comment_custom ?? col.comment_db ?? '');
    setPk(!!col.is_pk);
    setPart(!!col.is_partition);
    setBucket(!!col.is_bucket);
    setStdId(col.field_standard_id ?? '');
  }
  const boundStd = standards.find((s) => s.id === stdId);
  const dimName = dims.find((d) => d.id === boundStd?.dimension_standard_id)?.name;

  return (
    <Drawer
      open={col != null}
      title={
        <>
          编辑字段 · <span className="mono-cell">{tableName}.{col?.name ?? ''}</span>
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button
            variant="accent"
            data-action="保存字段编辑"
            onClick={() =>
              onSave({
                comment_custom: cmt,
                is_pk: pk,
                is_partition: part,
                is_bucket: bucket,
                field_standard_id: stdId === '' ? null : stdId,
              })
            }
          >
            保存
          </Button>
        </>
      }
    >
      {col && (
        <>
          <div className="form-row">
            <label className="form-label">字段名 / 类型</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="form-input mono-cell" value={col.name} disabled />
              <input className="form-input mono-cell" value={col.type} disabled style={{ width: 150, flexShrink: 0 }} />
            </div>
            <div className="form-hint">结构信息来自摄取，在线库不可改；离线库支持修正（走元数据编辑通道）。</div>
          </div>
          <div className="form-row">
            <label className="form-label">键属性</label>
            <div style={{ display: 'flex', gap: 18, padding: '6px 0' }}>
              {(
                [
                  ['主键', pk, setPk],
                  ['分区键', part, setPart],
                  ['分桶键', bucket, setBucket],
                ] as const
              ).map(([label, val, set]) => (
                <label key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: 'var(--t2)', cursor: 'pointer' }}>
                  <span className={`mini-switch${val ? ' on' : ''}`} onClick={() => set(!val)}></span>
                  {label}
                </label>
              ))}
            </div>
            <div className="form-hint">摄取识别错误时人工修正；分区键变更会同步提示词注入与分区裁剪检查。</div>
          </div>
          <div className="form-row">
            <label className="form-label">注释（人工）</label>
            <input className="form-input" value={cmt} placeholder="业务语义，注入提示词" onChange={(e) => setCmt(e.target.value)} />
          </div>
          <div className="form-row">
            <label className="form-label">字段标准</label>
            <select className="form-select" value={stdId} onChange={(e) => setStdId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— 未绑定 —</option>
              {standards.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label className="form-label">维度标准（随字段标准带出）</label>
            <div style={{ fontSize: 13, color: 'var(--t2)' }}>{dimName ? <span className="dim-chip">{dimName}</span> : '—'}</div>
          </div>
        </>
      )}
    </Drawer>
  );
}

/** 表信息抽屉（原型 tdDrawer） */
function TableInfoDrawer({
  open,
  table,
  onClose,
  onSave,
}: {
  open: boolean;
  table: TableMeta;
  onClose: () => void;
  onSave: (body: Parameters<typeof tablesApi.patch>[1]) => void;
}) {
  const [cmt, setCmt] = useState(table.comment_custom ?? '');
  const [part, setPart] = useState<string>(table.partition_type ?? '');
  return (
    <Drawer
      open={open}
      title={
        <>
          编辑表信息 · <span className="mono-cell">{table.name}</span>
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="accent" data-action="保存表信息" onClick={() => onSave({ comment_custom: cmt, partition_type: (part || null) as 'month' | 'day' | null })}>
            保存
          </Button>
        </>
      }
    >
      <div className="form-row">
        <label className="form-label">表名</label>
        <input className="form-input mono-cell" value={table.name} disabled />
        <div className="form-hint">表名来自摄取，不可修改。</div>
      </div>
      <div className="form-row">
        <label className="form-label">人工注释</label>
        <textarea className="form-input" rows={2} value={cmt} onChange={(e) => setCmt(e.target.value)} />
        <div className="form-hint">重摄取不会覆盖人工注释；注入提示词时优先于数据库注释。</div>
      </div>
      <div className="form-row">
        <label className="form-label">分区粒度</label>
        <select className="form-select" value={part} onChange={(e) => setPart(e.target.value)}>
          <option value="">无（小表/维表）</option>
          <option value="month">按月分区</option>
          <option value="day">按日分区</option>
        </select>
        <div className="form-hint">决定提示词中的分区提示与 M5 分区裁剪检查规则。</div>
      </div>
      <div className="form-row">
        <label className="form-label">行数估算</label>
        <input className="form-input mono-cell" value={fmtInt(table.row_estimate)} disabled />
        <div className="form-hint">用于成本预估黄警阈值判断；在线库每次摄取自动刷新。</div>
      </div>
    </Drawer>
  );
}

/** 手动添加关系行（原型 add-rel-row）：表.列 → 表.列 四下拉 + 添加 */
function AddRelRow({
  datasourceId,
  tables,
  current,
}: {
  datasourceId: number;
  tables: TableListItem[];
  current: string;
}) {
  const qc = useQueryClient();
  const [fromT, setFromT] = useState(current);
  const [toT, setToT] = useState('');
  const [fromC, setFromC] = useState('');
  const [toC, setToC] = useState('');

  const fromTable = tables.find((t) => t.name === fromT);
  const toTable = tables.find((t) => t.name === toT);
  const { data: fromDetail } = useQuery({
    queryKey: ['table', fromTable?.id],
    queryFn: () => tablesApi.get(fromTable!.id),
    enabled: fromTable != null,
  });
  const { data: toDetail } = useQuery({
    queryKey: ['table', toTable?.id],
    queryFn: () => tablesApi.get(toTable!.id),
    enabled: toTable != null,
  });

  const createMut = useMutation({
    mutationFn: () =>
      relationsApi.create({ datasource_id: datasourceId, from_table: fromT, from_col: fromC, to_table: toT, to_col: toC }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['relations', datasourceId] });
      toast('关系已添加', 'ok');
    },
    onError: (e) => toast(`添加失败：${(e as Error).message}`, 'err'),
  });

  return (
    <div className="add-rel-row">
      手动添加：
      <select value={fromT} onChange={(e) => { setFromT(e.target.value); setFromC(''); }}>
        {tables.map((t) => (
          <option key={t.id} value={t.name}>
            {t.name} · {t.comment || '缺注释'}
          </option>
        ))}
      </select>
      .
      <select value={fromC} onChange={(e) => setFromC(e.target.value)}>
        <option value="">选择字段</option>
        {(fromDetail?.columns ?? []).map((c) => (
          <option key={c.id} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
      →
      <select value={toT} onChange={(e) => { setToT(e.target.value); setToC(''); }}>
        <option value="">选择表</option>
        {tables.map((t) => (
          <option key={t.id} value={t.name}>
            {t.name} · {t.comment || '缺注释'}
          </option>
        ))}
      </select>
      .
      <select value={toC} onChange={(e) => setToC(e.target.value)}>
        <option value="">选择字段</option>
        {(toDetail?.columns ?? []).map((c) => (
          <option key={c.id} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
      <Button size="sm" variant="accent" data-action="添加关系" disabled={!fromT || !fromC || !toT || !toC || createMut.isPending} onClick={() => createMut.mutate()}>
        添加
      </Button>
    </div>
  );
}
