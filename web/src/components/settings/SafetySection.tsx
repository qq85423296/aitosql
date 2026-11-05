import type { SettingsMap } from '../../api/types';
import { Switch } from '../ui/Switch';
import { fmtInt } from '../../lib/format';

/** SafetySection（FR-C5 查询与安全）：数值默认 = SSOT §4；三层防线展示卡 */
export function SafetySection({
  settings,
  onChange,
}: {
  settings: SettingsMap;
  onChange: (p: Partial<SettingsMap>) => void;
}) {
  const num = (key: keyof SettingsMap, label: string, hint: string, suffix = '') => (
    <div className="setting-row" key={String(key)}>
      <div className="lab">
        <div className="name">{label}</div>
        <div className="hint">{hint}</div>
      </div>
      <div className="ctl">
        <input
          className="form-input"
          type="number"
          style={{ width: 120, textAlign: 'right' }}
          value={Number(settings[key] ?? 0)}
          onChange={(e) => onChange({ [key]: Number(e.target.value) })}
        />
        {suffix && <span className="proto-note" style={{ marginLeft: 6 }}>{suffix}</span>}
      </div>
    </div>
  );

  return (
    <div>
      <div className="banner banner-green" style={{ maxWidth: 'none', margin: '0 0 12px' }}>
        三层防线：R15 静态阻断（sqlglot AST）→ EXPLAIN 干跑成本黄警 → 执行超时矩阵
      </div>
      {num('query.auto_limit', '自动 LIMIT', '纯 SELECT 无 LIMIT 时 AST 注入追加')}
      {num('query.timeout_s', '语句超时', '执行层超时', 's')}
      {num('query.max_fix_rounds', '修正轮数上限', '超出硬上限即交付错误卡')}
      {num('query.pool_size', '工作连接数 / 数据源', '管理连接独立（不经 validate/R15，供 KILL 寻址）')}
      {num('query.explain_timeout_s', 'EXPLAIN 干跑超时', '超时按 E3001 跳过干跑', 's')}
      {num('query.explain_scan_warn_rows', '成本黄警阈值', `当前 ${fmtInt(Number(settings['query.explain_scan_warn_rows']))} 行`)}
      {num('query.ch_client_timeout_s', 'CH 客户端超时', '超时矩阵', 's')}
      <div className="setting-row">
        <div className="lab">
          <div className="name">写操作确认门</div>
          <div className="hint">query.write_confirm：写操作必须人工确认（F2）</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['query.write_confirm']} label="写操作确认门" data-action="开关切换" onChange={(v) => onChange({ 'query.write_confirm': v })} />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">API 访问认证</div>
          <div className="hint">server.auth：关闭后本机免 token 访问（仅建议单机自用；他人可及此机时请保持开启）</div>
        </div>
        <div className="ctl">
          <Switch checked={settings['server.auth'] !== false} label="API 访问认证" data-action="开关切换" onChange={(v) => onChange({ 'server.auth': v })} />
        </div>
      </div>
      <div className="setting-row">
        <div className="lab">
          <div className="name">ClickHouse readonly=1</div>
          <div className="hint">query.ch_readonly：强制（MySQL 只读为建议级，D05）</div>
        </div>
        <div className="ctl">
          <Switch checked={!!settings['query.ch_readonly']} label="CH readonly" data-action="开关切换" onChange={(v) => onChange({ 'query.ch_readonly': v })} />
        </div>
      </div>
    </div>
  );
}
