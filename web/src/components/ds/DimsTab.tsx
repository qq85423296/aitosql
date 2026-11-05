import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { dimensionsApi, standardsApi } from '../../api/standards';
import type { DimensionStandard, DimensionValue } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';

/**
 * DimsTab（FR-D11 维度标准，原型 Tab）：dim-cards 卡片网格（枚举值 dim-val + 引用计数）+
 * 新建/编辑抽屉（枚举值行编辑）；枚举值注入上限 schema.enum_top_n=8。
 */
export function DimsTab() {
  const qc = useQueryClient();
  const { data: list = [] } = useQuery({ queryKey: ['dimensions'], queryFn: dimensionsApi.list });
  const { data: standards = [] } = useQuery({ queryKey: ['standards'], queryFn: standardsApi.list });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<DimensionStandard | null>(null);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [vals, setVals] = useState<{ value: string; label: string }[]>([]);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['dimensions'] });
  const saveMut = useMutation({
    mutationFn: () => {
      const body = { name, description: desc, values: vals.filter((v) => v.value || v.label) as unknown as DimensionValue[] };
      return editing ? dimensionsApi.patch(editing.id, body) : dimensionsApi.create(body);
    },
    onSuccess: () => {
      invalidate();
      setOpen(false);
      toast('维度标准已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => dimensionsApi.remove(id),
    onSuccess: () => {
      invalidate();
      toast('维度标准已删除', 'ok');
    },
  });

  const refsOf = (d: DimensionStandard) => standards.filter((s) => s.dimension_standard_id === d.id).length;
  const totalRefs = list.reduce((a, d) => a + refsOf(d), 0);

  const openNew = () => {
    setEditing(null);
    setName('');
    setDesc('');
    setVals([{ value: '', label: '' }]);
    setOpen(true);
  };
  const openEdit = (d: DimensionStandard) => {
    setEditing(d);
    setName(d.name);
    setDesc(d.description ?? '');
    setVals((d.values ?? []).map((v) => ({ value: v.value, label: v.label ?? '' })));
    setOpen(true);
  };

  return (
    <div>
      <div className="mech-bar">
        <Icon name="info" size={15} />
        <div>
          维度标准决定枚举值如何 <b>直译进 SQL</b>（如「已支付」→ paid），避免方言值错误。
        </div>
      </div>
      <div className="toolbar-row">
        <Button variant="accent" size="sm" data-action="新建维度标准" onClick={openNew}>
          <Icon name="plus" size={13} strokeWidth={2.5} />
          新建维度标准
        </Button>
        <span className="count-note">
          {list.length} 个维度 · 被 {totalRefs} 个字段标准引用
        </span>
      </div>
      <div className="dim-cards">
        {list.map((d) => (
          <div className="dim-card" key={d.id}>
            <h4>
              {d.name}
              <span className="tag-mini">{refsOf(d)} 引用</span>
            </h4>
            <div className="dc-sub">{d.description || '（无描述）'}</div>
            <div className="dim-vals">
              {(d.values ?? []).map((v) => (
                <span key={v.id} className="dim-val">
                  <b>{v.value}</b>
                  {v.label}
                </span>
              ))}
            </div>
            <div className="dc-acts">
              <button className="row-act" data-action="编辑维度" onClick={() => openEdit(d)}>
                编辑
              </button>
              <button className="row-act danger" data-action="删除维度" onClick={() => delMut.mutate(d.id)}>
                删除
              </button>
            </div>
          </div>
        ))}
      </div>
      {list.length === 0 && <div className="empty-state">暂无维度标准</div>}

      <Drawer
        open={open}
        title={editing ? '编辑维度标准' : '新建维度标准'}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button variant="accent" data-action="保存标准" disabled={!name} onClick={() => saveMut.mutate()}>保存</Button>
          </>
        }
      >
        <div className="form-row">
          <label className="form-label">维度名 *</label>
          <input className="form-input" placeholder="如：渠道" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="form-row">
          <label className="form-label">描述</label>
          <input className="form-input" placeholder="如：订单来源渠道" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </div>
        <div className="form-row">
          <label className="form-label">枚举值</label>
          {vals.map((v, i) => (
            <div className="enum-row" key={i}>
              <input
                className="form-input"
                style={{ width: 110, flexShrink: 0 }}
                placeholder="值"
                value={v.value}
                onChange={(e) => setVals((p) => p.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
              />
              <input
                className="form-input"
                placeholder="中文含义"
                value={v.label}
                onChange={(e) => setVals((p) => p.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
              />
              <button className="enum-del" aria-label="删除枚举值" onClick={() => setVals((p) => p.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
          <span className="enum-add" data-action="添加枚举值" onClick={() => setVals((p) => [...p, { value: '', label: '' }])}>
            <Icon name="plus" size={12} strokeWidth={2.5} />
            添加枚举值
          </span>
          <div className="form-hint">超过 8 个枚举值时，仅将最常见的 8 个注入提示词（防超长）。</div>
        </div>
      </Drawer>
    </div>
  );
}
