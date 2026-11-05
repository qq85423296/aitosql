import { useEffect, useRef, useState } from 'react';
import { useSessionStore } from '../../stores/sessionStore';
import { Button } from '../ui/Button';

/**
 * Composer（FR-Q2/Q3，DSH 对齐改版）：
 * - 运行中不再锁死输入框（契约 1/2）：输入后两个动作——
 *   「引导当前轮」（POST /queries/:id/steer，kind=steer）/「排队下一轮」（正常 POST /queries，queued 回执）
 * - Ctrl+Enter 发送（运行中 = 排队下一轮）；离线库提示「仅生成不执行」（F7/D04）；
 * - 修正假设/自定义口径预填（F3/F4）；Esc 停止管线；右下字符数统计 dock。
 */
export function Composer({
  offline,
  running,
  disabled,
  onSend,
  onSteer,
  onStop,
}: {
  offline?: boolean;
  /** 有活动查询（running，不含排队壳）：输入框保持可用，动作切为 引导/排队/停止 */
  running?: boolean;
  disabled?: boolean;
  onSend: (question: string) => void;
  /** 引导当前轮（steer）；仅 running 时展示 */
  onSteer?: (content: string) => void;
  onStop?: () => void;
}) {
  const draft = useSessionStore((s) => s.composerDraft);
  const setDraft = useSessionStore((s) => s.setComposerDraft);
  const prefill = useSessionStore((s) => s.composerPrefill);
  const clearPrefill = useSessionStore((s) => s.clearPrefill);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [hint, setHint] = useState('');

  useEffect(() => {
    if (prefill != null) {
      setDraft(prefill);
      clearPrefill();
      taRef.current?.focus();
      setHint('已预填修正内容，请补充后发送');
      setTimeout(() => setHint(''), 3000);
    }
  }, [prefill, setDraft, clearPrefill]);

  const send = () => {
    const q = draft.trim();
    if (!q || disabled) return;
    onSend(q);
    setDraft('');
  };

  const steer = () => {
    const q = draft.trim();
    if (!q) return;
    onSteer?.(q);
    setDraft('');
    setHint('引导已发送，待当前轮领取生效');
    setTimeout(() => setHint(''), 3000);
  };

  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-box">
          <textarea
            ref={taRef}
            rows={2}
            placeholder={
              running
                ? '查询进行中：可「引导当前轮」或「排队下一轮」…'
                : offline
                  ? '离线库：仅生成不执行（SQL 可复制）…'
                  : '用自然语言描述你的查询…'
            }
            value={draft}
            disabled={disabled && !running}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send();
              if (e.key === 'Escape') onStop?.();
            }}
            aria-label="提问输入框"
          />
          {running ? (
            <>
              <Button variant="ghost" size="sm" data-action="引导当前轮" disabled={!draft.trim()} onClick={steer} title="输入内容将作为 steer 引导注入当前运行轮">
                引导当前轮
              </Button>
              <Button variant="accent" size="sm" data-action="排队下一轮" disabled={!draft.trim()} onClick={send} title="Ctrl/⌘+Enter">
                排队 ⏎
              </Button>
              {onStop && (
                <Button variant="danger" size="sm" data-action="停止管线" onClick={onStop} title="Esc">
                  ■ 停止
                </Button>
              )}
            </>
          ) : (
            <Button variant="accent" data-action="发送提问" onClick={send} disabled={!draft.trim() || disabled}>
              发送 ⏎
            </Button>
          )}
        </div>
        <div className="composer-hint">
          <span>
            {running
              ? '运行中 · Ctrl/⌘+Enter 排队下一轮 · Esc 停止'
              : offline
                ? '离线库 · 只跑到静态校验（validate）'
                : 'Ctrl/⌘+Enter 发送 · Esc 停止'}
          </span>
          <span>
            {hint}
            {hint ? ' · ' : ''}
            {draft.length} 字
          </span>
        </div>
      </div>
    </div>
  );
}
