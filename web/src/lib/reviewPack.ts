/**
 * 复核包模板（C9.1，SSOT §11.6 逐字原文）——前端常量，复制即留档。
 * 模板：
 * 【复核包】
 * 问题：{question}
 * 口径假设：{assumptions 逐条}
 * SQL：{sql_final}
 * 执行：{executed_at} · 用时 {duration_ms}ms · {rows} 行 · 扫描 {scan_rows}
 * 数据时间：{data_max_time}
 * 风险标记：{risks 或 "无"}
 * 结果校验（Q-14 修复：区分三态，不得把「未通过」与「未执行」混为一谈误导复核人）：
 *   passed=true  → 通过
 *   passed=false → 未通过（已照常交付）
 *   passed=null  → 未执行校验（离线库 / 未触发 verify 节点）
 */
export interface ReviewPackInput {
  question: string;
  assumptions: string[];
  sql: string;
  executedAt: string; // 本地时区 YYYY-MM-DD HH:mm
  durationMs: number | null;
  rows: number | null;
  scanRows: number | null;
  dataMaxTime: string | null;
  risks: string[]; // 空 = 无
  verifyPassed: boolean | null; // null = 未执行过校验（离线等）
}

export function buildReviewPack(i: ReviewPackInput): string {
  const lines = [
    '【复核包】',
    `问题：${i.question}`,
    `口径假设：${i.assumptions.length ? i.assumptions.join('；') : '无'}`,
    `SQL：${i.sql}`,
    `执行：${i.executedAt} · 用时 ${i.durationMs ?? '—'}ms · ${i.rows ?? '—'} 行 · 扫描 ${i.scanRows ?? '—'}`,
    `数据时间：${i.dataMaxTime ?? '—'}`,
    `风险标记：${i.risks.length ? i.risks.join('；') : '无'}`,
    `结果校验：${
      i.verifyPassed == null ? '未执行校验（离线/未触发）' : i.verifyPassed ? '通过' : '未通过（已照常交付）'
    }`,
  ];
  return lines.join('\n');
}
