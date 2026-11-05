import type { SettingsMap } from '../../api/types';
import { Switch } from '../ui/Switch';

/** SampleCacheSection（FR-C 样例缓存分组）：few-shot 条数 / 缓存开关 / 自动置过期 */
export function SampleCacheSection({
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
          <div className="name">few-shot 条数</div>
          <div className="hint">sample.fewshot_n（默认 3）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            type="number"
            style={{ width: 120, textAlign: 'right' }}
            value={Number(settings['sample.fewshot_n'] ?? 3)}
            onChange={(e) => onChange({ 'sample.fewshot_n': Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">重复问题缓存</div>
          <div className="hint">sample.cache：semantic_rev 判等命中</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['sample.cache']} label="重复问题缓存" data-action="开关切换" onChange={(v) => onChange({ 'sample.cache': v })} />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">schema 变更自动置过期</div>
          <div className="hint">sample.auto_stale：引用被删/改名字段即 stale（原因记字段名）</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['sample.auto_stale']} label="自动置过期" data-action="开关切换" onChange={(v) => onChange({ 'sample.auto_stale': v })} />
        </div>
      </div>
    </div>
  );
}
