import type { SettingsMap } from '../../api/types';

/** SchemaSection（FR-C6 Schema 注入）：预算滑块联动示意条；滞后带 55-65% 说明 */
export function SchemaSection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (p: Partial<SettingsMap>) => void;
}) {
  const budget = Number(settings['schema.budget_pct'] ?? 60);
  return (
    <div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">注入模式</div>
          <div className="hint">schema.mode：auto / full / retrieval（滞后带 55-65%）</div>
        </div>
        <div className="ctl">
          <select
            className="form-select"
            style={{ width: 160 }}
            value={String(settings['schema.mode'] ?? 'auto')}
            onChange={(e) => onChange({ 'schema.mode': e.target.value })}
          >
            <option value="auto">auto（滞后带）</option>
            <option value="full">full（全量）</option>
            <option value="retrieval">retrieval（检索）</option>
          </select>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">Schema 预算占比：{budget}%</div>
          <div className="hint">schema.budget_pct：注入占上下文比例</div>
        </div>
        <div className="ctl" style={{ width: 260 }}>
          <input
            type="range"
            min={10}
            max={90}
            value={budget}
            data-action="预算滑块"
            style={{ width: '100%', accentColor: 'var(--accent)' }}
            onChange={(e) => onChange({ 'schema.budget_pct': Number(e.target.value) })}
          />
          <div className="capacity-bar" style={{ marginTop: 6 }}>
            <div style={{ width: `${budget}%` }} />
          </div>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">向量检索</div>
          <div className="hint">schema.vector：vec 表重建期临时 off，降级纯 FTS（C6.13）</div>
        </div>
        <div className="ctl">
          <select
            className="form-select"
            style={{ width: 160 }}
            value={String(settings['schema.vector'] ?? 'auto')}
            onChange={(e) => onChange({ 'schema.vector': e.target.value })}
          >
            <option value="auto">auto</option>
            <option value="on">on</option>
            <option value="off">off</option>
          </select>
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">维度枚举注入上限</div>
          <div className="hint">schema.enum_top_n（默认 8）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            type="number"
            style={{ width: 120, textAlign: 'right' }}
            value={Number(settings['schema.enum_top_n'] ?? 8)}
            onChange={(e) => onChange({ 'schema.enum_top_n': Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">嵌入模型</div>
          <div className="hint">schema.embed_model（D01 定版 BAAI/bge-small-zh-v1.5）· 维度 {String(settings['schema.embed_dim'] ?? 512)}（换模型 → 重建 vec 表）</div>
        </div>
        <div className="ctl">
          <input
            className="form-input"
            value={String(settings['schema.embed_model'] ?? '')}
            onChange={(e) => onChange({ 'schema.embed_model': e.target.value })}
          />
        </div>
      </div>
    </div>
  );
}
