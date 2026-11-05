import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { backupApi } from '../../api/backup';
import type { SettingsMap } from '../../api/types';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';
import { toast } from '../ui/Toast';

/** BackupSection（FR-C8 备份）：VACUUM INTO 滚动 keep=10；剥离密文；恢复提示含「不适用自动续跑」 */
export function BackupSection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (p: Partial<SettingsMap>) => void;
}) {
  const qc = useQueryClient();
  // C1.6 冻结包络（P1-10）：list={items, restore_hint}；create={file}
  const { data: listRes } = useQuery({ queryKey: ['backups'], queryFn: backupApi.list });
  const list = listRes?.items ?? [];
  const restoreHint = listRes?.restore_hint ?? '恢复后进行中的查询一律标记失败，不适用自动续跑';
  const createMut = useMutation({
    mutationFn: backupApi.create,
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['backups'] });
      toast(`备份完成：${r.file}`, 'ok');
    },
    onError: (e) => toast(`备份失败：${(e as Error).message}`, 'err'),
  });

  return (
    <div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">立即备份</div>
          <div className="hint">
            目录 {String(settings['backup.dir'] ?? 'data/backups/')} · 滚动保留 {String(settings['backup.keep'] ?? 10)} 份；
            密钥密文剥离（写 NULL）；checkpoints.db / exports/ 不入备份
          </div>
        </div>
        <div className="ctl">
          <Button size="sm" data-action="立即备份" onClick={() => createMut.mutate()} disabled={createMut.isPending}>
            立即备份
          </Button>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">备份加密</div>
          <div className="hint">backup.encrypt（D08；不覆盖 exports/）</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['backup.encrypt']} label="备份加密" data-action="开关切换" onChange={(v) => onChange({ 'backup.encrypt': v })} />
        </div>
      </div>
      {/* FR-C8：恢复提示文案以后端 restore_hint 为准（随列表下发，单点收口） */}
      <div className="banner banner-amber" style={{ maxWidth: 'none', margin: '12px 0' }}>
        恢复语义：{restoreHint}
      </div>
      <table className="dt">
        <thead>
          <tr><th>文件</th><th>大小</th><th>时间</th></tr>
        </thead>
        <tbody>
          {list.map((b) => (
            <tr key={b.name}>
              <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{b.name}</td>
              <td>{(b.size / 1024 / 1024).toFixed(1)} MB</td>
              <td className="proto-note">{new Date(b.created_at).toLocaleString('zh-CN')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
