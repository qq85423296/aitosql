import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { providersApi } from '../../api/providers';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';
import { ProviderDrawer } from './ProviderDrawer';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { Provider } from '../../api/types';

/** ProviderList（FR-C1）：提供商卡片列表 + 启停 + 抽屉编辑 */
export function ProviderList() {
  const qc = useQueryClient();
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: providersApi.list });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);

  const toggleMut = useMutation({
    mutationFn: (p: Provider) => providersApi.patch(p.id, { enabled: p.enabled ? 0 : 1 }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['providers'] }),
  });

  return (
    <div>
      {providers.map((p) => (
        <div className="provider-card" key={p.id}>
          <div className="icon">{p.type === 'local' ? '本' : '云'}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{p.name}</div>
            <div className="proto-note">
              {p.base_url} · {p.models.join('、') || '（无模型）'}
            </div>
          </div>
          <Switch
            checked={!!p.enabled}
            label={`启用 ${p.name}`}
            onChange={() => toggleMut.mutate(p)}
          />
          <Button size="sm" data-action="打开提供商抽屉" onClick={() => { setEditing(p); setDrawerOpen(true); }}>
            管理
          </Button>
        </div>
      ))}
      <Button variant="accent" size="sm" onClick={() => { setEditing(null); setDrawerOpen(true); }}>
        ＋ 新增提供商
      </Button>
      <ProviderDrawer open={drawerOpen} provider={editing} onClose={() => setDrawerOpen(false)} />
    </div>
  );
}
