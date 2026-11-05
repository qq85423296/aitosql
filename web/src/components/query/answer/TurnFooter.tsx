import { useState } from 'react';
import { clsx } from 'clsx';
import type { MessageModel } from '../../../api/types';
import { isTerminal } from '../../../stores/queryStore';
import { fmtDuration, fmtInt, parseRouteRef } from '../../../lib/format';
import { toast } from '../../ui/Toast';

/**
 * TurnFooter（DSH 对齐 D+H）：每条已完成查询的轮次页脚——
 * 左侧：耗时（result_meta.duration_ms 权威，缺省回退 Σ stage.elapsed）· tokens（meta 事件）· 当前模型路由（meta.route.generate）；
 * 右侧动作：重试 / 复制SQL / 存为样例 / 👍 / 👎。
 * H：👍/👎 暂存 localStorage（键 aitosql.feedback）。
 * TODO(backend-feedback)：后端反馈 API 上线后，mark() 改为上报 POST /queries/:id/feedback。
 */

type Feedback = 'up' | 'down' | null;
const FB_KEY = 'aitosql.feedback';

function readFeedback(qid: string): Feedback {
  try {
    const map = JSON.parse(localStorage.getItem(FB_KEY) ?? '{}') as Record<string, 'up' | 'down'>;
    return map[qid] ?? null;
  } catch {
    return null;
  }
}

function writeFeedback(qid: string, v: Feedback): void {
  try {
    const map = JSON.parse(localStorage.getItem(FB_KEY) ?? '{}') as Record<string, 'up' | 'down'>;
    if (v == null) delete map[qid];
    else map[qid] = v;
    localStorage.setItem(FB_KEY, JSON.stringify(map));
  } catch {
    /* localStorage 不可用（隐私模式等）静默降级 */
  }
}

export function TurnFooter({
  msg,
  onRetry,
  onSaveSample,
}: {
  msg: MessageModel;
  /** 重试：以原问题重新发起一轮 */
  onRetry?: () => void;
  /** 存为样例（POST /queries/:id/save-sample，Stream 承载） */
  onSaveSample?: () => void;
}) {
  const [fb, setFb] = useState<Feedback>(() => readFeedback(msg.query_id));

  if (!isTerminal(msg.status)) return null;

  // F10 同口径：成功终态优先 result_meta.duration_ms（后端权威计时）
  const totalMs =
    msg.status === 'done' && msg.resultMeta?.duration_ms != null
      ? msg.resultMeta.duration_ms
      : msg.stages.reduce((acc, s) => acc + (s.elapsed_ms ?? 0), 0);
  const tokens = msg.meta?.tokens;
  const routeRef = msg.meta?.route?.generate;
  const model = parseRouteRef(routeRef)?.model ?? routeRef ?? null;

  const mark = (v: Exclude<Feedback, null>) => {
    const next: Feedback = fb === v ? null : v; // 再点取消
    setFb(next);
    writeFeedback(msg.query_id, next);
  };

  const copySql = async () => {
    if (!msg.sql) return;
    await navigator.clipboard.writeText(msg.sql);
    toast('SQL 已复制', 'ok');
  };

  return (
    <div className="turn-footer">
      <span>耗时 {fmtDuration(totalMs)}</span>
      {/* R2 收尾：历史回放可能只有 hit_tables 无 tokens（映射回退 0），不显示 0 */}
      {tokens != null && tokens > 0 && <span>tokens {fmtInt(tokens)}</span>}
      {model && <span title={routeRef ?? undefined}>模型 {model}</span>}
      <span className="tf-gap" />
      {onRetry && (
        <button className="tf-btn" data-action="重试提问" onClick={onRetry}>
          重试
        </button>
      )}
      {msg.sql && (
        <button className="tf-btn" data-action="复制SQL" onClick={() => void copySql()}>
          复制SQL
        </button>
      )}
      {onSaveSample && (
        <button className="tf-btn" data-action="页脚存为样例" onClick={onSaveSample}>
          存为样例
        </button>
      )}
      <button
        className={clsx('tf-btn', fb === 'up' && 'active')}
        data-action="反馈有用"
        title="有用"
        aria-pressed={fb === 'up'}
        onClick={() => mark('up')}
      >
        👍
      </button>
      <button
        className={clsx('tf-btn', fb === 'down' && 'active')}
        data-action="反馈无用"
        title="无用"
        aria-pressed={fb === 'down'}
        onClick={() => mark('down')}
      >
        👎
      </button>
    </div>
  );
}
