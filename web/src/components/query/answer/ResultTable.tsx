import { useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { buildReviewPack } from '../../../lib/reviewPack';
import { fmtDuration, fmtInt, fmtRows } from '../../../lib/format';
import { toast } from '../../ui/Toast';
import { Button } from '../../ui/Button';
import { queriesApi } from '../../../api/queries';
import type { MessageModel } from '../../../api/types';

/**
 * ResultTable（FR-Q9/Q10）：列排序 / 导出 CSV / 复核包 / 存为样例 / 复制 Markdown。
 * V1 守则：>100 行虚拟化（行高 32 / overscan 10）。
 * 导出 CSV toast 带「文件保留 24 小时」（export.ttl_h=24，TODO-N9 默认假设落位）。
 */
export function ResultTable({
  msg,
  onSaveSample,
}: {
  msg: MessageModel;
  onSaveSample?: () => void;
}) {
  const result = msg.result;
  const meta = msg.resultMeta;
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Q-9 修复：行稳定标识 = 行内容 JSON + 重复序号（排序后 key 不漂移，杜绝 index key 错位渲染）
  const keyedRows = useMemo(() => {
    if (!result) return [];
    const seen = new Map<string, number>();
    return result.rows.map((row) => {
      const base = JSON.stringify(row);
      const n = (seen.get(base) ?? 0) + 1;
      seen.set(base, n);
      return { key: `${base}#${n}`, row };
    });
  }, [result]);

  const sortedRows = useMemo(() => {
    if (sortCol == null) return keyedRows;
    const rows = [...keyedRows];
    rows.sort((a, b) => {
      const va = a.row[sortCol];
      const vb = b.row[sortCol];
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp =
        typeof va === 'number' && typeof vb === 'number'
          ? va - vb
          : String(va).localeCompare(String(vb), 'zh-CN');
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [keyedRows, sortCol, sortAsc]);

  const virtualize = sortedRows.length > 100; // V1 阈值
  const virtualizer = useVirtualizer({
    count: virtualize ? sortedRows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 32, // V1 行高 32px
    overscan: 10, // V1 overscan 10
  });

  if (!result) return null;

  const copyReviewPack = async () => {
    const pack = buildReviewPack({
      question: msg.question,
      assumptions: msg.assumptions.map((a) => a.text),
      sql: msg.sql ?? '',
      executedAt: msg.created_at ? new Date(msg.created_at).toLocaleString('zh-CN') : '',
      durationMs: meta?.duration_ms ?? null,
      rows: meta?.rows ?? result.rows.length,
      scanRows: meta?.scan_rows ?? null,
      dataMaxTime: meta?.data_max_time ?? null,
      risks: msg.risks.map((r) => `${r.rule_id} ${r.message}`),
      verifyPassed: msg.verify ? msg.verify.passed : null,
    });
    await navigator.clipboard.writeText(pack);
    toast('复核包已复制', 'ok');
  };

  const copyMarkdown = async () => {
    const header = `| ${result.columns.map((c) => c.name).join(' | ')} |`;
    const sep = `| ${result.columns.map(() => '---').join(' | ')} |`;
    const lines = result.rows.map((r) => `| ${r.map((v) => (v == null ? '' : String(v))).join(' | ')} |`);
    await navigator.clipboard.writeText([header, sep, ...lines].join('\n'));
    toast('结果 Markdown 已复制', 'ok');
  };

  const exportCsv = async () => {
    try {
      await queriesApi.exportCsv(msg.query_id);
      toast('已导出 CSV（UTF-8 带 BOM），文件保留 24 小时', 'ok');
    } catch (e) {
      toast(`导出失败：${(e as Error).message}`, 'err');
    }
  };

  const foot: string[] = [];
  if (meta) {
    foot.push(`${fmtRows(meta.rows)}`, `执行 ${fmtDuration(meta.duration_ms)}`, `扫描 ${fmtRows(meta.scan_rows)}`);
    if (meta.data_max_time) foot.push(`数据截至 ${meta.data_max_time}`);
    foot.push(meta.truncated ? 'LIMIT 1000 · 已截断' : '未截断');
  }

  const renderRow = (row: unknown[], key?: React.Key) => (
    <tr key={key}>
      {row.map((v, i) => (
        <td key={i}>{v == null ? <span style={{ color: 'var(--t3)' }}>NULL</span> : String(v)}</td>
      ))}
    </tr>
  );

  // Q-9 修复：colgroup 固定等宽列，表头表与虚拟行表共用同一列宽定义，消除列宽漂移
  const colPct = result ? `${(100 / Math.max(1, result.columns.length)).toFixed(4)}%` : '100%';
  const colgroup = (
    <colgroup>
      {result.columns.map((c) => (
        <col key={c.name} style={{ width: colPct }} />
      ))}
    </colgroup>
  );
  const thead = (
    <thead>
      <tr>
        {result.columns.map((c, i) => (
          <th
            key={c.name}
            className="sortable"
            data-action="结果排序"
            onClick={() => {
              if (sortCol === i) setSortAsc(!sortAsc);
              else {
                setSortCol(i);
                setSortAsc(true);
              }
            }}
            title={`${c.type}${sortCol === i ? (sortAsc ? '（升序）' : '（降序）') : ''}`}
          >
            {c.name} {sortCol === i ? (sortAsc ? '▲' : '▼') : ''}
            <span style={{ color: 'var(--t3)', fontWeight: 400 }}> {c.type}</span>
          </th>
        ))}
      </tr>
    </thead>
  );

  return (
    <div className="result-wrap">
      <div className="result-toolbar">
        <Button size="sm" variant="ghost" data-action="导出CSV" onClick={exportCsv}>
          导出 CSV
        </Button>
        <Button size="sm" variant="ghost" data-action="复制复核包" onClick={copyReviewPack}>
          复制复核包
        </Button>
        <Button size="sm" variant="ghost" data-action="复制结果Markdown" onClick={copyMarkdown}>
          复制 Markdown
        </Button>
        {onSaveSample && (
          <Button size="sm" variant="ghost" data-action="存为样例" onClick={onSaveSample}>
            存为样例
          </Button>
        )}
      </div>
      <div className="result-scroll" ref={scrollRef}>
        {virtualize ? (
          <>
            {/* 虚拟化（V1）：表头表与每行独立 table 共用 colgroup 等宽定义，保证列对齐 */}
            <table className="dt rtable" style={{ tableLayout: 'fixed', width: '100%' }}>
              {colgroup}
              {thead}
            </table>
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {virtualizer.getVirtualItems().map((vi) => (
                <table
                  key={sortedRows[vi.index].key}
                  className="dt rtable"
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    tableLayout: 'fixed',
                    transform: `translateY(${vi.start}px)`,
                  }}
                >
                  {colgroup}
                  <tbody>{renderRow(sortedRows[vi.index].row)}</tbody>
                </table>
              ))}
            </div>
          </>
        ) : (
          <table className="dt rtable">
            {thead}
            <tbody>{sortedRows.map(({ key, row }) => renderRow(row, key))}</tbody>
          </table>
        )}
      </div>
      {foot.length > 0 && (
        <div className="result-foot">
          {foot.map((f, i) => (
            <span key={i}>{f}</span>
          ))}
          {msg.meta?.tokens != null && <span>tokens {fmtInt(msg.meta.tokens)}</span>}
        </div>
      )}
    </div>
  );
}
