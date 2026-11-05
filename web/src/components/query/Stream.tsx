import { useEffect, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useQueryClient } from '@tanstack/react-query';
import type { Assumption, MessageModel } from '../../api/types';
import { queriesApi } from '../../api/queries';
import { useQueryStore } from '../../stores/queryStore';
import { useSessionStore } from '../../stores/sessionStore';
import { fmtDuration } from '../../lib/format';
import { toast } from '../ui/Toast';
import { AnswerCard } from './answer/AnswerCard';
import { AssumptionChips } from './answer/AssumptionChips';
import { ClarifyCard } from './answer/ClarifyCard';
import { SqlBlock } from './answer/SqlBlock';
import { ConfirmBar } from './answer/ConfirmBar';
import { ResultTable } from './answer/ResultTable';
import { TrajectoryBlock } from './answer/TrajectoryBlock';
import { ErrorCard } from './answer/ErrorCard';
import { Followups } from './answer/Followups';
import { TurnFooter } from './answer/TurnFooter';

/** 折叠四档（DSH 对齐 E）：0 展开 → 1 摘要（首行+耗时）→ 2 仅结论（结果表）→ 3 全部折叠 → 回 0 */
const COLLAPSE_LABEL = ['展开', '摘要', '仅结论', '全部折叠'] as const;
const COLLAPSE_GLYPH = ['▾', '≡', '▣', '▸'] as const;

/**
 * Stream（FR-Q3~Q11/Q14 消息流）：单列居中 max-width 760px。
 * V2 守则：>200 条虚拟滚动 + measureElement/ResizeObserver + 底部锚定。
 * 消息渲染 = SSE 事件装配态驱动（不再用 #state=，真实 store 态）。
 * DSH 对齐增量：轮次头部折叠四档循环（E）/ 排队小条（契约 1）/ inbox_claimed 系统提示（契约 3）/ TurnFooter（D+H）。
 * R2：过程区改为 TrajectoryBlock 结构化轨迹时间线（reasoning_full 全文思考 / 各阶段条目 / 修正轮差异概览）；
 *    ReasoningBlock / StageCard / CorrectionTimeline / VerifyBlock 不再引用（文件保留兼容，信息均由轨迹条目承载）。
 */
export function Stream({
  messages,
  followupsEnabled,
  onSend,
  onStopQuery,
}: {
  messages: MessageModel[];
  followupsEnabled: boolean;
  onSend: (q: string) => void;
  onStopQuery: (qid: string) => void;
}) {
  const qc = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);
  const decideGate = useQueryStore((s) => s.decideGate);
  const gateDecisions = useQueryStore((s) => s.gateDecisions);
  const prefillComposer = useSessionStore((s) => s.prefillComposer);
  const [histOpen, setHistOpen] = useState<Record<string, boolean>>({}); // 历史段折叠态
  const [collapseMap, setCollapseMap] = useState<Record<string, number>>({}); // 折叠四档（默认 0 展开）

  const virtualize = messages.length > 200; // V2 阈值
  const virtualizer = useVirtualizer({
    count: virtualize ? messages.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 220,
    measureElement: (el) => el.getBoundingClientRect().height, // V2 measureElement
    overscan: 8,
  });

  // V2 底部锚定：新消息到达时滚到底
  const lastStatus = messages[messages.length - 1]?.status;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, lastStatus]);

  // 滚动条自动隐藏：仅滚动中可见（停滚 900ms 后隐藏）
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      el.classList.add('scrolling');
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => el.classList.remove('scrolling'), 900);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (timer) clearTimeout(timer);
    };
  }, []);

  const cycleCollapse = (qid: string) =>
    setCollapseMap((p) => ({ ...p, [qid]: ((p[qid] ?? 0) + 1) % 4 }));

  /** 门裁决（CAS）：clarify 支持多选 choices + 自定义补充 custom（契约 4，向后兼容单选 choice） */
  const gateDecide = async (
    qid: string,
    gate: 'clarify' | 'confirm',
    payload: { choice?: string; choices?: string[]; custom?: string; approved?: boolean },
  ) => {
    decideGate(qid, { gate, ...payload }); // optimistic 回显（D-20）
    try {
      await queriesApi.resume(qid, { gate, ...payload });
    } catch (e) {
      toast(`门裁决失败：${(e as Error).message}（409 = 状态已变化，请刷新）`, 'err');
      void qc.invalidateQueries({ queryKey: ['queries'] });
    }
  };

  const saveSample = async (qid: string) => {
    try {
      await queriesApi.saveSample(qid);
      toast('已存为样例（同步直写 memory_log）', 'ok');
      void qc.invalidateQueries({ queryKey: ['samples'] });
    } catch (e) {
      toast(`存样例失败：${(e as Error).message}`, 'err');
    }
  };

  const renderAnswer = (m: MessageModel) => {
    const decision = gateDecisions[m.query_id];
    const running = m.status === 'running' && !m.queued; // 排队壳不算活动轮
    // R3 流式时序铁律：SQL 卡只在收到 sql 事件后出现（sqlRounds 仅由 sql 事件写入）；
    // 非运行语境（终态/等待门/历史回放）以 contract/sql_final 的 msg.sql 兜底展示
    const sqlVisible = !!m.sql && (Object.keys(m.sqlRounds ?? {}).length > 0 || !running);
    return (
      <div className="msg-answer" key={m.query_id}>
        {/* 时间线（dsh 式）：历史段默认折叠居上；当前过程组位于内容下方；新结果继续向下追加 */}
        {(m.history ?? []).map((h, i) => (
          <div className="fold-block hist-seg" key={i}>
            <button
              className="fold-hd"
              aria-expanded={!!histOpen[`${m.query_id}:${i}`]}
              onClick={() =>
                setHistOpen((p) => ({ ...p, [`${m.query_id}:${i}`]: !p[`${m.query_id}:${i}`] }))
              }
              data-action="折叠展开历史段"
            >
              <span aria-hidden>{histOpen[`${m.query_id}:${i}`] ? '▾' : '▸'}</span>
              <span className="fold-title">历史 · 第 {i + 1} 轮生成（{h.sql ? '含 SQL' : '无 SQL'}）</span>
            </button>
            {histOpen[`${m.query_id}:${i}`] && (
              <div className="fold-bd">
                {h.reasoning && <div className="fold-text">{h.reasoning}</div>}
                {h.sql && (
                  <SqlBlock
                    sql={h.sql}
                    dialect={m.dialect}
                    offline={m.ds_mode === 'offline'}
                    onRerun={(edited) => onSend(`按修改后的 SQL 重新执行：\n${edited}`)}
                  />
                )}
              </div>
            )}
          </div>
        ))}
        {/* R3 DOM 铁律：① 过程组（思考/阶段/修正/校验/执行全在里面）在最上方 */}
        {hasTrajectory(m, running) && (
          <TrajectoryBlock msg={m} running={running} onStop={() => onStopQuery(m.query_id)} />
        )}
        {/* ② 门裁决交互件（过程组的等待态出口） */}
        {m.status === 'waiting_confirm' && !decision && (
          <ConfirmBar
            risks={m.risks}
            estRows={m.est_rows}
            onDecide={(approved) => gateDecide(m.query_id, 'confirm', { approved })}
          />
        )}
        {decision?.gate === 'confirm' && (
          <ConfirmBar risks={m.risks} estRows={m.est_rows} decision={{ approved: !!decision.approved }} />
        )}
        {m.status === 'waiting_clarify' && !decision && m.clarify_options && (
          <ClarifyCard
            options={m.clarify_options}
            onDecide={(sel) =>
              gateDecide(m.query_id, 'clarify', {
                choices: sel.choices,
                custom: sel.custom,
                // 向后兼容：恰好单选且无自定义时回传 choice（旧后端合成路径）
                ...(sel.choices.length === 1 && !sel.custom ? { choice: sel.choices[0] } : {}),
              })
            }
          />
        )}
        {(decision?.gate === 'clarify' || m.clarify_choice) && m.clarify_options && (
          <ClarifyCard
            options={m.clarify_options}
            decided={decision?.choice ?? m.clarify_choice}
            decidedChoices={decision?.choices}
            decidedCustom={decision?.custom}
          />
        )}
        {/* ③ 最终回复（永远在最后、任何展示模式不隐藏）：回答卡 + 假设 chips + SQL 卡 + 结果表 */}
        {m.answer && <AnswerCard text={m.answer} />}
        <AssumptionChips
          assumptions={m.assumptions}
          onFix={(a: Assumption) => prefillComposer(`更正假设：${a.text} 不对，应该是：`)}
        />
        {sqlVisible && (
          <SqlBlock
            sql={m.sql!}
            dialect={m.dialect}
            writeOp={m.risks.some((r) => r.rule_id === 'R06')}
            offline={m.ds_mode === 'offline'}
            unstructured={m.contractUnstructured}
            onRerun={(edited) => onSend(`按修改后的 SQL 重新执行：\n${edited}`)}
          />
        )}
        {m.result && <ResultTable msg={m} onSaveSample={() => saveSample(m.query_id)} />}
        {m.error && (
          <ErrorCard
            code={m.error.code}
            message={m.error.message}
            retryable={m.error.retryable}
            dialect={m.dialect}
            onRetry={m.error.retryable ? () => onSend(m.question) : undefined}
            onSwitchRoute={() => (window.location.href = '/settings#g-route')}
          />
        )}
        <Followups items={m.followups} enabled={followupsEnabled} onPick={onSend} />
        {/* ④ 轮次页脚操作行（回复之后垫底；MetaNote 已并入轨迹信封组装条目 + 页脚 tokens/路由，不再单挂） */}
        <TurnFooter
          msg={m}
          onRetry={() => onSend(m.question)}
          onSaveSample={() => saveSample(m.query_id)}
        />
      </div>
    );
  };

  /** R2：本轮是否有可展示的轨迹内容（无则摘要档退化为单行摘要条） */
  const hasTrajectory = (m: MessageModel, running: boolean) =>
    running ||
    m.stages.length > 0 ||
    (m.reasoningFull?.length ?? 0) > 0 ||
    !!m.sql ||
    !!m.resultMeta ||
    !!m.verify ||
    m.corrections.length > 0;

  /** E-摘要档兜底：无轨迹内容时的单行摘要（首行+耗时），点击继续循环 */
  const renderSummary = (m: MessageModel) => {
    const totalMs =
      m.status === 'done' && m.resultMeta?.duration_ms != null
        ? m.resultMeta.duration_ms
        : m.stages.reduce((acc, s) => acc + (s.elapsed_ms ?? 0), 0);
    const firstLine = (m.answer ?? m.reasoning ?? m.sql ?? '').split('\n', 1)[0].slice(0, 60);
    return (
      <div className="fold-block">
        <button
          className="fold-hd"
          data-action="折叠档位切换"
          title="折叠档位：摘要（点击循环）"
          onClick={() => cycleCollapse(m.query_id)}
        >
          <span aria-hidden>≡</span>
          <span className="fold-title">
            {firstLine || m.question}
            {(m.answer ?? m.reasoning ?? '').length > 60 ? '…' : ''}
          </span>
          <span className="tree-elapsed">{fmtDuration(totalMs)}</span>
        </button>
      </div>
    );
  };

  /** E-仅结论档：回答卡（meta 直通）+ 结果表 + 错误卡 + 轮次页脚 */
  const renderConclusion = (m: MessageModel) => (
    <div className="msg-answer">
      {m.answer && <AnswerCard text={m.answer} />}
      {m.result && <ResultTable msg={m} onSaveSample={() => saveSample(m.query_id)} />}
      {m.error && (
        <ErrorCard
          code={m.error.code}
          message={m.error.message}
          retryable={m.error.retryable}
          dialect={m.dialect}
          onRetry={m.error.retryable ? () => onSend(m.question) : undefined}
          onSwitchRoute={() => (window.location.href = '/settings#g-route')}
        />
      )}
      {!m.answer && !m.result && !m.error && <div className="proto-note">（本轮无结果产物）</div>}
      <TurnFooter msg={m} onRetry={() => onSend(m.question)} onSaveSample={() => saveSample(m.query_id)} />
    </div>
  );

  const renderMsg = (m: MessageModel) => {
    const mode = collapseMap[m.query_id] ?? 0;
    const running = m.status === 'running' && !m.queued;
    return (
      <div key={m.query_id} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="msg-user">{m.question}</div>
        {/* 轮次头部（E）：点击循环切换折叠档位 */}
        <div className="turn-head">
          <button
            className="collapse-cycle"
            data-action="折叠档位切换"
            title={`折叠档位：${COLLAPSE_LABEL[mode]}（点击循环）`}
            onClick={() => cycleCollapse(m.query_id)}
          >
            {COLLAPSE_GLYPH[mode]} {COLLAPSE_LABEL[mode]}
          </button>
        </div>
        {m.queued ? (
          /* 契约 1：排队回执小条——当前轮终态后 followup_started 自动开轮 */
          <div className="queued-note" role="status">
            已排队，当前轮完成后自动开始…
          </div>
        ) : mode === 3 ? (
          <button className="collapsed-bar" data-action="折叠档位切换" onClick={() => cycleCollapse(m.query_id)}>
            ▸ 已折叠（点击恢复：{COLLAPSE_LABEL[(mode + 1) % 4]}）
          </button>
        ) : mode === 1 ? (
          /* E-摘要档（R2 集成）：轨迹每条一行（图标+标题+耗时）；无轨迹内容退化单行摘要条 */
          hasTrajectory(m, running) ? (
            <TrajectoryBlock msg={m} running={running} variant="compact" />
          ) : (
            renderSummary(m)
          )
        ) : mode === 2 ? (
          renderConclusion(m)
        ) : (
          renderAnswer(m)
        )}
      </div>
    );
  };

  return (
    <div className="stream" ref={scrollRef}>
      <div className="stream-inner">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="big">向数据库提问，得到可复核的 SQL 与结果</div>
            <div>支持 MySQL / Doris / ClickHouse 方言；写操作需确认门；结论附复核包</div>
          </div>
        )}
        {virtualize ? (
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
            {virtualizer.getVirtualItems().map((vi) => (
              <div
                key={vi.key}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${vi.start}px)` }}
              >
                {renderMsg(messages[vi.index])}
              </div>
            ))}
          </div>
        ) : (
          messages.map(renderMsg)
        )}
      </div>
    </div>
  );
}
