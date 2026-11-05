/**
 * mockPipeline —— mock 模式下的查询事件发生器：按 C3.2 十六事件序列驱动一条演示查询，
 * 带单调递增 seq（帧 id = {query_id}:{seq}），验证装配五步的幂等/横幅逻辑。
 */
import type { SseEvent } from './types';

/** seq=null 表示非落库事件（result 等，C3.2 冻结：帧不写 id，前端旁路幂等，P0-2） */
type Emit = (ev: SseEvent, seq: number | null) => void;

const DEMO_SQL =
  "SELECT c.channel_name, DATE_TRUNC('month', o.created_at) AS month,\n  COUNT(*) AS order_cnt, SUM(o.amount) AS gmv\nFROM orders o JOIN channels c ON o.channel_id = c.id\nWHERE o.created_at >= '2026-08-01' AND o.created_at < '2026-10-01'\nGROUP BY 1, 2\nORDER BY channel_name, month;";

export function mockRunPipeline(
  queryId: string,
  emit: Emit,
  opts: { offline?: boolean } = {},
): () => void {
  let seq = 0;
  let stopped = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  const step = (ms: number, fn: () => void) => {
    timers.push(
      setTimeout(() => {
        if (!stopped) fn();
      }, ms),
    );
  };
  const send = (ev: SseEvent) => emit(ev, ++seq);

  step(100, () => send({ type: 'stage', stage: 'supply', round: 0, status: 'start' }));
  step(500, () => send({ type: 'stage', stage: 'supply', round: 0, status: 'done', elapsed_ms: 400 }));
  step(520, () =>
    send({ type: 'meta', phase: 'post_supply', hit_tables: ['orders', 'channels'], route: { generate: '1:qwen3-32b', fix: '1:qwen3-32b' }, tokens: 3214 }),
  );
  step(600, () => send({ type: 'stage', stage: 'generate', round: 1, status: 'start' }));
  // R2：gen_delta 实时思考/生成输出演示（非落库事件：帧无 id → seq=null，旁路幂等直接应用）
  step(700, () =>
    emit(
      { type: 'gen_delta', stage: 'generate', round: 1, kind: 'reasoning', text: '用户问「上个月各渠道销售额环比」…\n先定位口径：销售额 → orders.amount（含税）；环比 → 自然月分组 + LAG 窗口。' },
      null,
    ),
  );
  step(1100, () =>
    emit(
      { type: 'gen_delta', stage: 'generate', round: 1, kind: 'content', text: 'WITH monthly AS (\n  SELECT c.channel_name, DATE_TRUNC(\'month\', o.created_at) AS month, …' },
      null,
    ),
  );
  step(1400, () =>
    send({
      type: 'contract',
      contract: {
        reasoning: '关联 orders × channels，按自然月分组聚合',
        assumptions: [
          { text: '"上个月" → 2026-09-01 ~ 09-30', impact: 'low', kind: 'time' },
          { text: 'channel_id → channels.id', impact: 'high', kind: 'relation' },
        ],
        need_tables: ['orders', 'channels'],
        sql: DEMO_SQL,
        followups: ['按渠道钻取到城市维度', '环比改成同比去年同月'],
      },
    }),
  );
  // R2 新持久事件 reasoning_full：完整思考全文（落库、有 seq；轨迹「思考」条目全文来源）
  step(1420, () =>
    send({
      type: 'reasoning_full',
      round: 0,
      text: '用户问题：上个月各渠道销售额环比。\n\n1. 口径识别：销售额默认含税（orders.amount）；时间范围「上个月」→ 2026-09-01 ~ 09-30（分区键 created_at，月分区裁剪）。\n2. 维度关联：渠道名称在 channels.channel_name，orders.channel_id → channels.id（推测关系，置信 0.85，已在假设中声明）。\n3. 环比计算：按 channel × 月聚合 GMV 后，LAG(gmv) OVER (PARTITION BY channel ORDER BY month) 取上月值，(本月/上月 - 1) × 100 为环比百分比。\n4. 风险自查：排除 is_test=1 / is_deleted=1；8 月数据完整、9 月完整，环比可比。',
    }),
  );
  step(1450, () => send({ type: 'sql', sql: DEMO_SQL, round: 1 }));
  step(1500, () => send({ type: 'stage', stage: 'generate', round: 1, status: 'done', elapsed_ms: 900 }));
  step(1550, () => send({ type: 'stage', stage: 'validate', round: 1, status: 'start' }));
  step(1900, () => send({ type: 'stage', stage: 'validate', round: 1, status: 'done', elapsed_ms: 350 }));
  if (opts.offline) {
    // F7/D04 离线库：只跑到 validate，不执行；R13 黄警照常交付；done unverified=true
    step(2000, () =>
      send({
        type: 'risk',
        risks: [{ rule_id: 'R13', level: 'yellow', message: '离线库：仅生成不执行（SQL 可复制到目标库运行）' }],
      }),
    );
    step(2100, () => send({ type: 'followups', items: ['把这条 SQL 改造成统计月度趋势'] }));
    step(2200, () => send({ type: 'done', query_id: queryId, status: 'done', unverified: true }));
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
    };
  }
  step(1950, () => send({ type: 'stage', stage: 'execute', round: 1, status: 'start' }));
  step(2600, () =>
    send({ type: 'result_meta', rows: 4, scan_rows: 842000, duration_ms: 650, data_max_time: '2026-09-30', truncated: false }),
  );
  // result 事件模拟真实后端：不落 query_event 表、帧无 id → seq=null（验证 P0-2 旁路幂等路径）
  step(2650, () =>
    emit(
      {
        type: 'result',
        columns: [
          { name: 'channel_name', type: 'varchar' },
          { name: 'month', type: 'datetime' },
          { name: 'order_cnt', type: 'bigint' },
          { name: 'gmv', type: 'decimal' },
        ],
        rows: [
          ['SEM 投放', '2026-09', 4207, '551,940.50'],
          ['自然流量', '2026-09', 6981, '768,002.15'],
          ['短视频', '2026-09', 1873, '228,506.75'],
          ['老客召回', '2026-09', 1356, '235,144.00'],
        ],
      },
      null,
    ),
  );
  step(2700, () => send({ type: 'stage', stage: 'execute', round: 1, status: 'done', elapsed_ms: 750 }));
  step(2750, () => send({ type: 'stage', stage: 'verify', round: 1, status: 'start' }));
  step(3100, () =>
    send({
      type: 'verify',
      checks: [
        { name: '行数非零（4 行）', passed: true },
        { name: '分区裁剪：命中分区键 created_at', passed: true },
      ],
      passed: true,
      fallback_used: false,
    }),
  );
  step(3150, () => send({ type: 'stage', stage: 'verify', round: 1, status: 'done', elapsed_ms: 350 }));
  step(3200, () =>
    send({ type: 'followups', items: ['按渠道钻取到城市维度', '只看短视频渠道每日明细', '环比改成同比去年同月'] }),
  );
  step(3250, () =>
    send({ type: 'meta', phase: 'pre_done', hit_tables: ['orders', 'channels'], route: { generate: '1:qwen3-32b', fix: '1:qwen3-32b' }, tokens: 3214 }),
  );
  step(3300, () => send({ type: 'done', query_id: queryId, status: 'done', unverified: false }));

  return () => {
    stopped = true;
    timers.forEach(clearTimeout);
  };
}
