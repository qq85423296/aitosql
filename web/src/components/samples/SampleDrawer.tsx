import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { samplesApi } from '../../api/samples';
import { datasourcesApi } from '../../api/datasources';
import type { Sample } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { toast } from '../ui/Toast';

/** SampleDrawer（FR-S2 新增/编辑）：必填校验；question_norm 归一化由服务端做（小写/去空白/去标点） */
export function SampleDrawer({
  open,
  sample,
  onClose,
}: {
  open: boolean;
  sample?: Sample | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const [dsId, setDsId] = useState<number>(sample?.datasource_id ?? 0);
  const [question, setQuestion] = useState(sample?.question ?? '');
  const [sql, setSql] = useState(sample?.sql ?? '');
  const [err, setErr] = useState('');

  useEffect(() => {
    if (open) {
      setDsId(sample?.datasource_id ?? datasources[0]?.id ?? 0);
      setQuestion(sample?.question ?? '');
      setSql(sample?.sql ?? '');
      setErr('');
    }
  }, [open, sample, datasources]);

  const saveMut = useMutation({
    mutationFn: () =>
      sample
        ? samplesApi.patch(sample.id, { question, sql })
        : samplesApi.create({ datasource_id: dsId, question, sql }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['samples'] });
      onClose();
      toast('样例已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });

  const save = () => {
    if (!question.trim()) return setErr('问题不能为空');
    if (!sql.trim()) return setErr('SQL 不能为空');
    if (!sample && !dsId) return setErr('请选择数据源');
    setErr('');
    saveMut.mutate();
  };

  return (
    <Drawer
      open={open}
      title={sample ? '编辑样例' : '新增样例'}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="accent" data-action="保存样例" onClick={save} disabled={saveMut.isPending}>保存</Button>
        </>
      }
    >
      {!sample && (
        <>
          <label className="form-label">数据源 *</label>
          <select className="form-select" value={dsId} onChange={(e) => setDsId(Number(e.target.value))}>
            {datasources.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}（{d.dialect}）
              </option>
            ))}
          </select>
        </>
      )}
      <label className="form-label" style={{ marginTop: 10 }}>问题 *</label>
      <input className="form-input" value={question} onChange={(e) => setQuestion(e.target.value)} />
      <div className="proto-note" style={{ marginTop: 2 }}>
        缓存命中按归一化问题精确匹配（question_norm：小写/去空白/去标点）
      </div>
      <label className="form-label" style={{ marginTop: 10 }}>SQL *</label>
      <textarea className="form-textarea" rows={8} value={sql} onChange={(e) => setSql(e.target.value)} />
      {err && <div className="form-err">{err}</div>}
    </Drawer>
  );
}
