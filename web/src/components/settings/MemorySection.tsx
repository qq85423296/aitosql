import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { memoryApi } from '../../api/memory';
import type { SettingsMap } from '../../api/types';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';
import { toast } from '../ui/Toast';
import { fmtInt } from '../../lib/format';
import { clsx } from 'clsx';

/** MemorySection（FR-C7 记忆与偏好）：总账分组/开关/容量条（>80% 琥珀）/ 流水账只读 / 立即合并 */
export function MemorySection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (p: Partial<SettingsMap>) => void;
}) {
  const qc = useQueryClient();
  const { data: ledger = {} } = useQuery({ queryKey: ['memory'], queryFn: memoryApi.ledger });
  const { data: log = [] } = useQuery({ queryKey: ['memory-log'], queryFn: memoryApi.log });

  // C1.6 冻结（P1-8）：toggle 请求体 {enabled} 必填，前端显式传目标态
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => memoryApi.toggle(id, enabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['memory'] }),
    onError: (e) => toast(`切换失败：${(e as Error).message}`, 'err'),
  });
  const consolidateMut = useMutation({
    mutationFn: memoryApi.consolidate,
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['memory'] });
      void qc.invalidateQueries({ queryKey: ['memory-log'] });
      toast(`合并完成：并入 ${r.merged} 条，丢弃 ${r.dropped} 条`, 'ok');
    },
  });

  const maxTokens = Number(settings['memory.max_tokens'] ?? 4000);
  const usedTokens = Object.values(ledger)
    .flat()
    .filter((m) => m.enabled)
    .reduce((s, m) => s + (m.token_est ?? 0), 0);
  const pct = Math.min(100, Math.round((usedTokens / maxTokens) * 100));

  return (
    <div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">记忆模块</div>
          <div className="hint">memory.enabled 总开关</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['memory.enabled']} label="记忆模块" data-action="开关切换" onChange={(v) => onChange({ 'memory.enabled': v })} />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">总账容量</div>
          <div className="hint">
            {fmtInt(usedTokens)} / {fmtInt(maxTokens)} tokens（&gt;80% 琥珀）
          </div>
        </div>
        <div className="ctl" style={{ width: 220, display: 'block' }}>
          <div className={clsx('capacity-bar', pct > 80 && 'warn')}>
            <div style={{ width: `${pct}%` }} />
          </div>
          <div className="proto-note" style={{ textAlign: 'right' }}>{pct}%</div>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">手动合并</div>
          <div className="hint">流水账 → 总账（水位线增量，最小间隔 {String(settings['memory.consolidate_min_gap_min'] ?? 30)} 分钟）</div>
        </div>
        <div className="ctl">
          <Button size="sm" data-action="立即合并记忆" onClick={() => consolidateMut.mutate()} disabled={consolidateMut.isPending}>
            立即合并
          </Button>
        </div>
      </div>

      <h4 style={{ margin: '16px 0 6px', fontSize: 13 }}>总账（停用即不注入）</h4>
      {Object.entries(ledger).map(([topic, items]) => (
        <div key={topic} style={{ marginBottom: 10 }}>
          <div className="session-group-hd">{topic}</div>
          {items.map((m) => (
            <div key={m.id} className="setting-row" style={{ padding: '6px 0' }}>
              <div className="lab" style={{ fontSize: 12.5 }}>
                {m.content}
                <span className="proto-note">（{m.scope}{m.datasource_id ? ` · ds#${m.datasource_id}` : ''} · ~{m.token_est} tok）</span>
              </div>
              <div className="ctl">
                <Switch checked={!!m.enabled} label={`启用 ${m.content}`} onChange={(v) => toggleMut.mutate({ id: m.id, enabled: v })} />
              </div>
            </div>
          ))}
        </div>
      ))}

      <h4 style={{ margin: '16px 0 6px', fontSize: 13 }}>流水账（只读）</h4>
      <div className="ingest-log">
        {log.map((l) => (
          <div key={l.id}>
            [{l.kind}] {l.fact}
          </div>
        ))}
        {log.length === 0 && <div>（空）</div>}
      </div>
    </div>
  );
}
