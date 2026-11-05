import { useQuery } from '@tanstack/react-query';
import { settingsApi } from '../../api/settings';
import { datasourcesApi } from '../../api/datasources';
import { Drawer } from '../ui/Drawer';
import { fmtInt } from '../../lib/format';

/**
 * PreviewDrawer（FR-C4 / C4.2）：组装预览 8 段信封 + 预算条（ctx 32768 / schema 60% / output 4096）。
 * P1-7 修复：datasource_id 为后端必填参数——设置页无数据源上下文，取第一可用数据源（contracts.md C4.2 冻结）。
 */
export function PreviewDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: datasources = [] } = useQuery({
    queryKey: ['datasources'],
    queryFn: datasourcesApi.list,
    enabled: open,
  });
  const dsId = datasources[0]?.id;
  const { data, error } = useQuery({
    queryKey: ['prompt-preview', dsId],
    queryFn: () => settingsApi.promptPreview(dsId!),
    enabled: open && dsId != null,
  });

  return (
    <Drawer open={open} wide title="提示词组装预览（8 段信封）" onClose={onClose}>
      {dsId == null ? (
        <div className="empty-state">暂无数据源：请先在「数据源」页注册后再预览组装信封</div>
      ) : error ? (
        <div className="empty-state">预览加载失败：{(error as Error).message}</div>
      ) : data ? (
        <>
          <div className="proto-note" style={{ marginBottom: 12 }}>
            数据源：{datasources[0].name} · 总 tokens {fmtInt(data.total_tokens)} · 上下文预算 {fmtInt(data.budget.ctx)} · Schema 占比上限 {data.budget.schema_pct}% · 输出预留 {fmtInt(data.budget.output_reserved)}
          </div>
          {data.segments.map((s, i) => (
            <div key={i} className="fold-block" style={{ marginBottom: 8 }}>
              <div className="fold-hd" style={{ cursor: 'default' }}>
                <span className="mini-badge">{i + 1}</span>
                <span style={{ flex: 1 }}>{s.name}</span>
                <span className="proto-note">{fmtInt(s.tokens)} tok</span>
              </div>
              {s.text && (
                <div className="fold-bd" style={{ fontFamily: 'var(--mono)', fontSize: 11.5, whiteSpace: 'pre-wrap' }}>
                  {s.text}
                </div>
              )}
            </div>
          ))}
        </>
      ) : (
        <div className="empty-state">加载中…</div>
      )}
    </Drawer>
  );
}
