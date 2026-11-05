import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import type { Datasource } from '../../api/types';
import { Button } from '../ui/Button';
import { Drawer } from '../ui/Drawer';
import { toast } from '../ui/Toast';

/**
 * InstructionDrawer（契约 5：数据源口径说明编辑器）：
 * 每个数据源一份口径文本——GET /datasources/:id/instruction 加载、PUT {content} 保存；
 * 内容将注入生成提示词的数据源口径段。
 * 防御：GET 包络缺 content 字段时按空串处理（契约缺口可选链）。
 */
export function InstructionDrawer({ ds, onClose }: { ds: Datasource | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [content, setContent] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['ds-instruction', ds?.id],
    queryFn: () => datasourcesApi.instruction.get(ds!.id),
    enabled: ds != null,
  });

  // 打开后同步服务端内容（包络字段缺失 → 空串）
  useEffect(() => {
    if (ds != null) setContent(data?.content ?? '');
  }, [ds, data]);

  const saveMut = useMutation({
    mutationFn: () => datasourcesApi.instruction.put(ds!.id, content),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ds-instruction', ds!.id] });
      toast('口径说明已保存（将注入生成提示词）', 'ok');
      onClose();
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });

  return (
    <Drawer
      open={ds != null}
      title={`口径说明 · ${ds?.name ?? ''}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="accent"
            data-action="保存口径说明"
            disabled={saveMut.isPending || isLoading}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <div className="form-hint" style={{ marginBottom: 8 }}>
        将注入生成提示词的数据源口径段：写清业务口径、默认过滤、禁用写法等。
      </div>
      {isError && <div className="form-hint" style={{ color: 'var(--red)', marginBottom: 8 }}>加载失败，可直接编辑后保存覆盖</div>}
      <textarea
        className="ddl-area"
        style={{ minHeight: 260 }}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={'例：\n· 「销售额」默认含税（orders.amount）\n· 统计默认排除 is_test=1 / is_deleted=1\n· 时间口径默认下单时间 created_at'}
        aria-label="数据源口径说明"
      />
      {isLoading && <div className="proto-note" style={{ marginTop: 6 }}>加载中…</div>}
    </Drawer>
  );
}
