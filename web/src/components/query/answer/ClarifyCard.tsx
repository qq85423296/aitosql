import { useState } from 'react';
import { clsx } from 'clsx';
import type { ClarifyOption } from '../../../api/types';
import { Button } from '../../ui/Button';

/**
 * ClarifyCard（F3/FR-Q8 澄清门，DSH 对齐多选版）：
 * 选项 checkbox 多选 + 「自定义补充」文本框 + 提交按钮；
 * 提交体 {gate:'clarify', choices:[...], custom}——单选场景由后端合成 choice（契约 4，向后兼容）。
 * 裁决走 POST /queries/:id/resume；命中 clarify_pref 免问（C6.7）。
 */
export function ClarifyCard({
  options,
  decided,
  decidedChoices,
  decidedCustom,
  onDecide,
}: {
  options: ClarifyOption[];
  /** 已裁决回显（clarify_choice 合成串，E5 快照含此字段） */
  decided?: string | null;
  /** 已裁决多选回显（optimistic 门裁决态，契约 4） */
  decidedChoices?: string[];
  /** 已裁决自定义补充回显（契约 4） */
  decidedCustom?: string;
  /** 提交裁决：多选 label 列表 + 自定义补充（可只填其一） */
  onDecide?: (sel: { choices: string[]; custom: string }) => void;
}) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [custom, setCustom] = useState('');

  // 已裁决回显：多选 + 自定义补充 + 兼容单选串
  if (decided || decidedChoices?.length || decidedCustom) {
    const parts = [
      ...(decidedChoices?.length ? decidedChoices : decided ? [decided] : []),
      ...(decidedCustom ? [`补充：${decidedCustom}`] : []),
    ];
    return (
      <div className="clarify-card" role="status">
        <span style={{ color: 'var(--green)' }}>已选择口径：{parts.join('；')}（已沉淀为口径偏好）</span>
      </div>
    );
  }

  const toggle = (i: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const submit = () => {
    const choices = [...selected].sort((a, b) => a - b).map((i) => options[i].label);
    onDecide?.({ choices, custom: custom.trim() });
  };

  const canSubmit = selected.size > 0 || custom.trim().length > 0;

  return (
    <div className="clarify-card">
      <strong style={{ color: 'var(--amber)' }}>存在高风险歧义，请选择口径（可多选，可自定义补充）</strong>
      {options.map((o, i) => (
        <button
          key={i}
          className={clsx('clarify-opt', selected.has(i) && 'selected')}
          data-action="裁决澄清"
          aria-pressed={selected.has(i)}
          onClick={() => toggle(i)}
        >
          <span className="clarify-opt-hd">
            <span className="clarify-check" aria-hidden>
              {selected.has(i) ? '☑' : '☐'}
            </span>
            <span>{o.label}</span>
          </span>
          <span className="expr">{o.expr}</span>
        </button>
      ))}
      <textarea
        className="clarify-custom"
        rows={2}
        placeholder="自定义补充（可选）：直接写明口径说明…"
        value={custom}
        onChange={(e) => setCustom(e.target.value)}
        aria-label="自定义口径补充"
      />
      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
        <Button size="sm" variant="accent" data-action="提交澄清裁决" disabled={!canSubmit} onClick={submit}>
          提交（自动续跑）
        </Button>
        {!canSubmit && <span className="proto-note">至少勾选一个口径，或填写自定义补充</span>}
      </div>
    </div>
  );
}
