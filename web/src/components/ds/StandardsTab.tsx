import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { standardsApi, dimensionsApi } from '../../api/standards';
import type { FieldStandard } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';

/**
 * StandardsTab（FR-D11 字段标准，原型 Tab）：mech-bar 注入说明 + 筛选/计数 +
 * std-panel 表格（类型约束/单位/绑定维度/取值说明/引用字段/操作）+ 新建/编辑抽屉。
 */
export function StandardsTab() {
  const qc = useQueryClient();
  const { data: list = [] } = useQuery({ queryKey: ['standards'], queryFn: standardsApi.list });
  const { data: dims = [] } = useQuery({ queryKey: ['dimensions'], queryFn: dimensionsApi.list });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<FieldStandard | null>(null);
  const [form, setForm] = useState<Partial<FieldStandard>>({});

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['standards'] });
  const saveMut = useMutation({
    mutationFn: () => (editing ? standardsApi.patch(editing.id, form) : standardsApi.create(form)),
    onSuccess: () => {
      invalidate();
      setOpen(false);
      toast('字段标准已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => standardsApi.remove(id),
    onSuccess: () => {
      invalidate();
      toast('字段标准已删除', 'ok');
    },
  });

  const totalRefs = list.reduce((a, s) => a + (s.refs ?? 0), 0);
  const openNew = () => {
    setEditing(null);
    setForm({});
    setOpen(true);
  };
  const openEdit = (s: FieldStandard) => {
    setEditing(s);
    setForm({ name: s.name, data_type: s.data_type ?? '', unit: s.unit ?? '', dimension_standard_id: s.dimension_standard_id ?? null, value_desc: s.value_desc ?? '', description: s.description ?? '' });
    setOpen(true);
  };

  return (
    <div>
      <div className="mech-bar">
        <Icon name="info" size={15} />
        <div>
          字段标准注入 <b>系统提示词</b>（类型/单位/值域），绑定后所有查询统一口径；改一处，全部查询立即生效。
        </div>
      </div>
      <div className="toolbar-row">
        <Button variant="accent" size="sm" data-action="新建字段标准" onClick={openNew}>
          <Icon name="plus" size={13} strokeWidth={2.5} />
          新建字段标准
        </Button>
        <select className="filter-select">
          <option>全部标准</option>
          <option>仅本库引用</option>
        </select>
        <span className="count-note">
          {list.length} 个标准 · 被 {totalRefs} 个字段引用
        </span>
      </div>
      <div className="std-panel">
        <table className="dt">
          <thead>
            <tr>
              <th>标准名</th>
              <th>类型约束</th>
              <th>单位</th>
              <th>绑定维度</th>
              <th>取值说明</th>
              <th style={{ textAlign: 'right' }}>引用字段</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.map((s) => {
              const dim = dims.find((d) => d.id === s.dimension_standard_id);
              return (
                <tr key={s.id}>
                  <td style={{ fontWeight: 600 }}>{s.name}</td>
                  <td className="mono-cell">{s.data_type || '—'}</td>
                  <td className="muted">{s.unit || '—'}</td>
                  <td>{dim ? <span className="dim-chip">{dim.name}</span> : <span className="muted">—</span>}</td>
                  <td className="t-comment-cell">{s.value_desc || '—'}</td>
                  <td className="num" style={{ textAlign: 'right', color: 'var(--accent)' }}>{s.refs ?? '—'}</td>
                  <td>
                    <div className="row-acts">
                      <button className="row-act" data-action="编辑标准" onClick={() => openEdit(s)}>
                        编辑
                      </button>
                      <button className="row-act danger" data-action="删除标准" onClick={() => delMut.mutate(s.id)}>
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {list.length === 0 && <div className="empty-state">暂无字段标准</div>}

      <Drawer
        open={open}
        title={editing ? '编辑字段标准' : '新建字段标准'}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button variant="accent" data-action="保存标准" disabled={!form.name} onClick={() => saveMut.mutate()}>保存</Button>
          </>
        }
      >
        <div className="form-row">
          <label className="form-label">标准名 *</label>
          <input className="form-input" placeholder="如：金额（含税）" value={form.name ?? ''} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">类型约束</label>
          <input className="form-input mono" placeholder="decimal(10,2)" value={form.data_type ?? ''} onChange={(e) => setForm((f) => ({ ...f, data_type: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">单位</label>
          <input className="form-input" placeholder="元 / 件 / 无" value={form.unit ?? ''} onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">绑定维度标准（可选）</label>
          <select
            className="form-select"
            value={form.dimension_standard_id ?? ''}
            onChange={(e) => setForm((f) => ({ ...f, dimension_standard_id: e.target.value ? Number(e.target.value) : null }))}
          >
            <option value="">— 不绑定 —</option>
            {dims.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label className="form-label">取值说明</label>
          <input className="form-input" placeholder="如：含税成交金额，退款为负" value={form.value_desc ?? ''} onChange={(e) => setForm((f) => ({ ...f, value_desc: e.target.value }))} />
        </div>
        <div className="form-row">
          <label className="form-label">备注（不注入提示词）</label>
          <textarea className="form-input" rows={2} placeholder="口径背景、负责人等内部信息" value={form.description ?? ''} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        </div>
      </Drawer>
    </div>
  );
}
