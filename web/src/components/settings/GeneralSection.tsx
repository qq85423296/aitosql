import type { SettingsMap } from '../../api/types';
import { Switch } from '../ui/Switch';
import { Button } from '../ui/Button';
import { toast } from '../ui/Toast';

/** GeneralSection（FR-C8 通用，spec TODO-N6 默认假设落位）：端口/会话压缩/导出 TTL/令牌 */
export function GeneralSection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (p: Partial<SettingsMap>) => void;
}) {
  return (
    <div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">服务端口</div>
          <div className="hint">server.port：监听 127.0.0.1（CORS/Origin 同限定）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            type="number"
            style={{ width: 120, textAlign: 'right' }}
            value={Number(settings['server.port'] ?? 8321)}
            onChange={(e) => onChange({ 'server.port': Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">会话压缩阈值</div>
          <div className="hint">session.compact_msgs：消息数超此值触发压缩（保留最近 keep_msgs 轮完整）</div>
        </div>
        <div className="ctl" style={{ display: 'flex', gap: 6 }}>
          <input
            className="form-input"
            type="number"
            style={{ width: 90, textAlign: 'right' }}
            value={Number(settings['session.compact_msgs'] ?? 40)}
            onChange={(e) => onChange({ 'session.compact_msgs': Number(e.target.value) })}
          />
          <input
            className="form-input"
            type="number"
            style={{ width: 90, textAlign: 'right' }}
            title="session.keep_msgs"
            value={Number(settings['session.keep_msgs'] ?? 6)}
            onChange={(e) => onChange({ 'session.keep_msgs': Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">导出 TTL</div>
          <div className="hint">export.ttl_h：CSV 瞬态目录定时清理（toast「文件保留 24 小时」）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            type="number"
            style={{ width: 120, textAlign: 'right' }}
            value={Number(settings['export.ttl_h'] ?? 24)}
            onChange={(e) => onChange({ 'export.ttl_h': Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">本机访问令牌</div>
          <div className="hint">server.token：首启生成，仅 CLI 打印一次；Bearer 鉴权</div>
        </div>
        <div className="ctl">
          <Button
            size="sm"
            variant="ghost"
            data-action="重新生成令牌"
            onClick={() => toast('令牌已重新生成（仅本次展示，请立即保存）', 'ok')}
          >
            重新生成
          </Button>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">记忆注入信封</div>
          <div className="hint">memory.inject：总账注入第 3 段</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['memory.inject']} label="记忆注入" data-action="开关切换" onChange={(v) => onChange({ 'memory.inject': v })} />
        </div>
      </div>
    </div>
  );
}
