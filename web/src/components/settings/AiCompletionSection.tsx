import type { SettingsMap } from '../../api/types';
import { Switch } from '../ui/Switch';

/** AiCompletionSection（方案 v1.1 §6/§3.1）：补全并发泳道数 + 采样值出境门控 */
export function AiCompletionSection({
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
          <div className="name">补全并发泳道数</div>
          <div className="hint">ai.completion_workers：整库补全同时处理的表数（1–6，默认 3）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            type="number"
            min={1}
            max={6}
            style={{ width: 100, textAlign: 'right' }}
            value={Number(settings['ai.completion_workers'] ?? 3)}
            onChange={(e) =>
              onChange({ 'ai.completion_workers': Math.max(1, Math.min(6, Number(e.target.value) || 3)) })
            }
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">允许采样值用于 AI 判断</div>
          <div className="hint">
            ai.sample_values_allowed：开启后补全任务可对在线库做 SELECT … LIMIT 50 采样（TOP-5 值/NULL 率进提示词）；
            默认关闭 = 采样值不出境，仅用元数据推断
          </div>
        </div>
        <div className="ctl">
          <Switch
            checked={!!settings['ai.sample_values_allowed']}
            label="采样值门控"
            data-action="开关切换"
            onChange={(v) => onChange({ 'ai.sample_values_allowed': v })}
          />
        </div>
      </div>
    </div>
  );
}
