import { useState } from 'react';
import type { SettingsMap } from '../../api/types';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';
import { PreviewDrawer } from './PreviewDrawer';
import { toast } from '../ui/Toast';

const DIALECT_TABS = ['mysql', 'doris', 'clickhouse'] as const;
const DIALECT_CARD: Record<(typeof DIALECT_TABS)[number], string> = {
  mysql: 'MySQL 能力卡：LIMIT 语法；禁用 INTO OUTFILE/DUMPFILE；EXPLAIN 静态干跑',
  doris: 'Doris 能力卡：DATE_TRUNC/LAG；OUTFILE s3:// 属出境通道（R15 阻断）',
  clickhouse: 'ClickHouse 能力卡：readonly=1 强制；无 KILL 通道；EXPLAIN 支持有限',
};

/**
 * PromptSection（FR-C4）：系统模板 / 方言能力卡 Tab / 行为开关 ×5 / 组装预览抽屉。
 * verify_llm 置灰联动（D07/F11）：全本地路由时禁用。
 */
export function PromptSection({
  settings,
  cloudRoute,
  onChange,
}: {
  settings: SettingsMap;
  cloudRoute: boolean; // 任一路由含云（RouteSection 判定后传入）
  onChange: (patch: Partial<SettingsMap>) => void;
}) {
  const [dialect, setDialect] = useState<(typeof DIALECT_TABS)[number]>('mysql');
  const [previewOpen, setPreviewOpen] = useState(false);

  const features: { key: keyof SettingsMap; label: string; hint: string; disabled?: boolean }[] = [
    { key: 'feature.clarify_gate', label: '澄清门', hint: '高风险歧义先问再跑（clarify_pref 命中免问）' },
    { key: 'feature.verify_rules', label: '结果校验规则层', hint: '行数/口径/汇总一致性静态规则' },
    {
      key: 'feature.verify_llm',
      label: 'LLM 校验层',
      hint: cloudRoute ? '结果前 5 行随 verify_llm 出境（E6 红标）' : 'local_only 置灰禁用（D07）',
      disabled: !cloudRoute,
    },
    { key: 'feature.followups', label: '追问建议', hint: '最多 3 条，点击即新提问' },
    { key: 'feature.partition_hint', label: '分区提示注入', hint: '命中分区键提示注入提示词' },
    { key: 'feature.sql_comments', label: 'SQL 详细备注', hint: '生成的 SQL 前部注入表/字段中文名注释块' },
  ];

  return (
    <div>
      <label className="form-label">系统提示词（prompt.system，可覆盖；{'{{date}}'} 注入本地 YYYY-MM-DD）</label>
      <textarea
        className="form-textarea"
        rows={6}
        style={{ fontFamily: 'var(--sans)' }}
        value={String(settings['prompt.system'] ?? '')}
        onChange={(e) => onChange({ 'prompt.system': e.target.value })}
      />
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <Button
          size="sm"
          variant="ghost"
          data-action="恢复默认系统提示词"
          onClick={() => {
            onChange({ 'prompt.system': '' });
            toast('已恢复默认系统提示词（置空 = 用后端默认）', 'ok');
          }}
        >
          恢复默认
        </Button>
        <Button
          size="sm"
          variant="ghost"
          data-action="校验模板变量"
          onClick={() => toast('模板变量校验通过：{{date}} {{schema}} {{samples}}', 'ok')}
        >
          校验变量
        </Button>
        <Button size="sm" variant="ghost" data-action="打开组装预览" onClick={() => setPreviewOpen(true)}>
          组装预览（8 段）
        </Button>
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="form-label">方言能力卡（prompt.dialect.*）</div>
        <div className="tabs" style={{ padding: 0 }}>
          {DIALECT_TABS.map((d) => (
            <button
              key={d}
              className={dialect === d ? 'tab active' : 'tab'}
              role="tab"
              data-action="切换方言能力卡"
              onClick={() => setDialect(d)}
            >
              {d}
            </button>
          ))}
        </div>
        <div className="fold-block" style={{ marginTop: 8 }}>
          <div className="fold-bd" style={{ paddingTop: 10 }}>{DIALECT_CARD[dialect]}</div>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        {features.map((f) => (
          <div className="setting-row" key={String(f.key)}>
            <div className="lab">
              <div className="name">{f.label}</div>
              <div className="hint">{f.hint}</div>
            </div>
            <div className="ctl">
              <Switch
                checked={!!settings[f.key]}
                disabled={f.disabled}
                label={f.label}
                data-action="行为开关"
                onChange={(v) => onChange({ [f.key]: v })}
              />
            </div>
          </div>
        ))}
      </div>
      <PreviewDrawer open={previewOpen} onClose={() => setPreviewOpen(false)} />
    </div>
  );
}
