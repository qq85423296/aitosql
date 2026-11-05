import { useEffect, useMemo, useRef, useState } from 'react';
import type { MessageModel, StageName, StageView } from '../../../api/types';
import { fmtDuration } from '../../../lib/format';

const STAGE_LABEL: Record<StageName, string> = {
  supply: 'Schema 供给',
  generate: '生成',
  validate: '静态校验',
  clarify: '澄清',
  confirm: '确认',
  execute: '执行',
  verify: '结果校验',
  correct: '修正',
};

/**
 * StageCard（dsh 风格树形过程卡，FR-Q3 体验改版）：
 * - 头部：进行中「进行中 · 当前阶段…」/ 完成自动折叠为「已完成，用时 X」（dsh 同款）；
 * - 一级：阶段行（chevron 展开，无对号图标；进行=accent 色；中断=红色「（中断）」后缀）；
 * - 二级：阶段子项——generate: 思考/生成输出（实时流）；validate: 风险行；correct: 轮次原因；
 * - 三级：子项正文（思考全文 / 流式输出），点开才显示，流式期间自动锚底。
 * - llm_retry（契约 3）：模型调用退避重试，渲染为卡内灰字小条「模型重试 第N次（failure_class，Xs 后）」。
 */
export function StageCard({
  msg,
  running,
  onStop,
}: {
  msg: MessageModel;
  running: boolean;
  onStop?: () => void;
}) {
  const stages = msg.stages;
  const totalMs = useMemo(
    // F10：成功终态优先 result_meta.duration_ms（后端权威计时）——Σ stage.elapsed
    // 在中断场景缺失进行中阶段的 elapsed（恒 0），总用时系统性偏低
    () => (msg.status === 'done' && msg.resultMeta?.duration_ms != null
      ? msg.resultMeta.duration_ms
      : stages.reduce((acc, s) => acc + (s.elapsed_ms ?? 0), 0)),
    [stages, msg.status, msg.resultMeta?.duration_ms],
  );
  const active = [...stages].reverse().find((s) => s.status === 'start');
  const activeKey = active ? `${active.stage}-${active.round}` : null;

  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [openThinking, setOpenThinking] = useState(false);
  const [openOutput, setOpenOutput] = useState(true);
  const thinkingRef = useRef<HTMLDivElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);

  const preview = msg.genPreview;
  const thinkingDone = !!preview?.content || !running;

  // dsh 行为：完成后整卡自动折叠；当前阶段自动展开；思考结束自动收起
  useEffect(() => {
    if (!running) setOpen(false);
  }, [running]);
  useEffect(() => {
    if (activeKey) setExpanded((prev) => new Set(prev).add(activeKey));
  }, [activeKey]);
  useEffect(() => {
    if (thinkingDone) setOpenThinking(false);
  }, [thinkingDone]);

  // 流式期间自动锚底
  useEffect(() => {
    const el = thinkingRef.current;
    if (el && openThinking) el.scrollTop = el.scrollHeight;
  }, [preview?.reasoning, openThinking]);
  useEffect(() => {
    const el = outputRef.current;
    if (el && openOutput) el.scrollTop = el.scrollHeight;
  }, [preview?.content, openOutput]);

  if (!stages.length && !running) return null;

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const renderKids = (s: StageView): React.ReactNode => {
    const kids: React.ReactNode[] = [];
    if (s.stage === 'generate' && preview) {
      if (preview.reasoning) {
        const tail = preview.reasoning.slice(-48).trimStart();
        kids.push(
          <div className="tree-row lv2" key="thinking">
            <button className="tree-node" onClick={() => setOpenThinking(!openThinking)} aria-expanded={openThinking}>
              <span className={`tree-chev ${openThinking ? 'open' : ''}`}>›</span>
              <span className="tree-label dim">
                {thinkingDone
                  ? (openThinking ? '思考过程' : '已完成思考')
                  : (openThinking ? '思考中' : <>思考中 · …{tail}</>)}
              </span>
            </button>
            {openThinking && (
              <div className="tree-body" ref={thinkingRef}>{preview.reasoning}</div>
            )}
          </div>,
        );
      }
      if (preview.content) {
        kids.push(
          <div className="tree-row lv2" key="output">
            <button className="tree-node" onClick={() => setOpenOutput(!openOutput)} aria-expanded={openOutput}>
              <span className={`tree-chev ${openOutput ? 'open' : ''}`}>›</span>
              <span className="tree-label dim">生成输出</span>
            </button>
            {openOutput && (
              <div className="tree-body output" ref={outputRef}>
                {preview.content}
                <span className="gen-cursor" aria-hidden />
              </div>
            )}
          </div>,
        );
      }
    }
    if (s.stage === 'validate' && msg.risks.length > 0) {
      for (const [i, r] of msg.risks.entries()) {
        kids.push(
          <div className="tree-row lv2 static" key={`risk-${i}`}>
            <span className="tree-node">
              <span className="tree-chev leaf" />
              <span className={`tree-label risk-${r.level}`}>
                {r.rule_id} · {r.message}
              </span>
            </span>
          </div>,
        );
      }
    }
    if (s.stage === 'correct') {
      const corr = msg.corrections.filter((c) => c.round === s.round);
      for (const [i, c] of corr.entries()) {
        kids.push(
          <div className="tree-row lv2 static" key={`corr-${i}`}>
            <span className="tree-node">
              <span className="tree-chev leaf" />
              <span className="tree-label dim">
                {c.error || '修正'} · 指纹 {c.fingerprint}
              </span>
            </span>
          </div>,
        );
      }
    }
    return kids.length ? <div className="tree-kids">{kids}</div> : null;
  };

  return (
    <div className="tree-card" aria-live="polite">
      <button className="tree-hd" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`tree-chev ${open ? 'open' : ''}`}>›</span>
        {running ? (
          <span className="tree-title">
            进行中 · {active ? STAGE_LABEL[active.stage] : '准备中'}…
          </span>
        ) : msg.status === 'failed' ? (
          <span className="tree-title failed">已失败，用时 {fmtDuration(totalMs)}</span>
        ) : msg.status === 'stopped' ? (
          <span className="tree-title done">已停止，用时 {fmtDuration(totalMs)}</span>
        ) : (
          <span className="tree-title done">已完成，用时 {fmtDuration(totalMs)}</span>
        )}
        {running && onStop && (
          <span
            className="tree-stop"
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onStop();
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.stopPropagation();
                onStop();
              }
            }}
          >
            ■ 停止（Esc）
          </span>
        )}
      </button>
      {open && (
        <div className="tree-bd">
          {stages.map((s) => {
            const key = `${s.stage}-${s.round}`;
            const kids = renderKids(s);
            const isOpen = expanded.has(key);
            const broken = s.status === 'start' && !running;
            return (
              <div className="tree-row" key={key}>
                <button
                  className="tree-node"
                  onClick={() => kids && toggle(key)}
                  disabled={!kids}
                  aria-expanded={kids ? isOpen : undefined}
                >
                  <span className={`tree-chev ${isOpen ? 'open' : ''} ${kids ? '' : 'leaf'}`}>›</span>
                  <span
                    className={`tree-label ${s.status === 'start' && running ? 'active' : ''} ${broken ? 'broken' : ''}`}
                  >
                    {STAGE_LABEL[s.stage]}
                    {s.round > 0 && <em className="tree-round">r{s.round}</em>}
                    {broken && <em className="tree-broken">（中断）</em>}
                    {s.status === 'start' && running && <span className="gen-cursor" aria-hidden />}
                  </span>
                  <span className="tree-elapsed">
                    {s.elapsed_ms != null ? fmtDuration(s.elapsed_ms) : ''}
                  </span>
                </button>
                {isOpen && kids}
              </div>
            );
          })}
          {/* 契约 3：llm_retry 灰字小条（模型调用退避重试提示） */}
          {(msg.retries ?? []).map((r, i) => (
            <div className="tree-row" key={`llm-retry-${i}`}>
              <span className="tree-node retry-note">
                <span className="tree-chev leaf" />
                <span className="tree-label dim">
                  模型重试 第 {r.attempt} 次（{r.failure_class}，{fmtDuration(r.delay_ms)}后
                  {r.route ? ` · ${r.route}` : ''}）
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
