import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { metricsApi } from '../../api/standards';
import type { MetricStandard } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';

/**
 * MetricsTab（FR-D11 指标口径，原型 Tab）：mech-bar + std-panel 表格
 * （metric-expr 紫高亮 / 过滤条件 / 基础表 / 说明 / 操作）+ 新建/编辑抽屉。
 * 保存时 sqlglot 校验，未知标识符黄警不阻断（C1.3/C6.5）；filter 保留列名（D-19）。
 */
export function MetricsTab() {
  const qc = useQueryClient();
  const { data: list = [] } = useQuery({ queryKey: ['metrics'], queryFn: metricsApi.list });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MetricStandard | null>(null);
  const [form, setForm] = useState<Partial<MetricStandard>>({});
  const [warnings, setWarnings] = useState<string[]>([]);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['metrics'] });
  const saveMut = useMutation({
    mutationFn: () =>
      editing ? metricsApi.patch(editing.id, form) : metricsApi.save({ ...form, name: form.name!, expr: form.expr! }),
    onSuccess: (r) => {
      invalidate();
      const w = 'warnings' in r ? (r.warnings ?? []) : [];
      if (w.length) {
        setWarnings(w); // 黄警不阻断：留在抽屉展示
        toast('已保存（存在未知标识符黄警）', 'ok');
      } else {
        setOpen(false);
        toast('指标已保存', 'ok');
      }
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => metricsApi.remove(id),
    onSuccess: () => {
      invalidate();
      toast('指标已删除', 'ok');
    },
  });

  const openNew = () => {
    setEditing(null);
    setForm({});
    setWarnings([]);
    setOpen(true);
  };
  const openEdit = (m: MetricStandard) => {
    setEditing(m);
    setForm({ name: m.name, expr: m.expr, filter: m.filter ?? '', base_tables: m.base_tables ?? '', description: m.description ?? '' });
    setWarnings([]);
    setOpen(true);
  };

  return (
    <div>
      <div className="mech-bar">
        <Icon name="info" size={15} />
        <div>
          指标口径以 <b>命名模板</b> 注入（「销售额」→ SUM(amount)）；复合指标展开一次，不递归。
        </div>
      </div>
      <div className="toolbar-row">
        <Button variant="accent" size="sm" data-action="新建指标口径" onClick={openNew}>
          <Icon name="plus" size={13} strokeWidth={2.5} />
          新建指标口径
        </Button>
        <span className="count-note">{list.length} 个指标</span>
      </div>
      <div className="std-panel">
        <table className="dt">
          <thead>
            <tr>
              <th>指标名</th>
              <th>口径表达式</th>
              <th>过滤条件</th>
              <th>基础表</th>
              <th>说明</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.map((m) => (
              <tr key={m.id}>
                <td style={{ fontWeight: 600 }}>{m.name}</td>
                <td>
                  <span className="metric-expr">{m.expr}</span>
                </td>
                <td>
                  <span className="metric-filter">{m.filter || '—'}</span>
                </td>
                <td className="mono-cell">{m.base_tables || '—'}</td>
                <td className="t-comment-cell">{m.description || '—'}</td>
                <td>
                  <div className="row-acts">
                    <button className="row-act" data-action="编辑指标" onClick={() => openEdit(m)}>
                      编辑
                    </button>
                    <button className="row-act danger" data-action="删除指标" onClick={() => delMut.mutate(m.id)}>
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length === 0 && <div className="empty-state">暂无指标口径</div>}

      <Drawer
        open={open}
        title={editing ? '编辑指标口径' : '新建指标口径'}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button variant="accent" data-action="保存指标" disabled={!form.name || !form.expr} onClick={() => saveMut.mutate()}>保存</Button>
          </>
        }
      >
        <div className="form-row">
          <label className="form-label">指标名 *</label>
          <input className="form-input" placeholder="如：GMV（成交总额）" value={form.name ?? ''} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">口径表达式 *</label>
          <textarea className="form-input mono" rows={2} placeholder="SUM(amount)" value={form.expr ?? ''} onChange={(e) => setForm((f) => ({ ...f, expr: e.target.value }))} />
          <div className="form-hint">支持基础四则 / 聚合；可引用其他指标名（复合指标）。保存时 sqlglot 校验，未知标识符黄警不阻断。</div>
        </div>
        <div className="form-row">
          <label className="form-label">过滤条件</label>
          <textarea className="form-input mono" rows={2} placeholder={"status IN ('paid','shipped','done') AND is_test = 0"} value={form.filter ?? ''} onChange={(e) => setForm((f) => ({ ...f, filter: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">基础表</label>
          <input className="form-input" placeholder="orders" value={form.base_tables ?? ''} onChange={(e) => setForm((f) => ({ ...f, base_tables: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">说明</label>
          <textarea className="form-input" rows={2} placeholder="口径背景说明（不注入）" value={form.description ?? ''} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        </div>
        {warnings.length > 0 && (
          <div className="banner banner-amber" style={{ maxWidth: 'none', margin: '10px 0 0' }}>
            <div>
              {warnings.map((w, i) => (
                <div key={i}>⚠ {w}</div>
              ))}
              <div className="proto-note">未知标识符黄警不阻断保存</div>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
