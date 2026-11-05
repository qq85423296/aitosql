import { useState } from 'react';

/**
 * ReasoningBlock：推理折叠块（FR-Q4，dsh 风格）。
 * 标题只显示短引子（首行 ≤60 字）；完整推理正文默认收起，点击展开。
 */
export function ReasoningBlock({ summary, steps }: { summary: string; steps?: string[] }) {
  const [open, setOpen] = useState(false);
  const firstLine = summary.split('\n', 1)[0];
  const teaser = firstLine.slice(0, 60);
  const truncated = summary.length > teaser.length;
  return (
    <div className="fold-block">
      <button
        className="fold-hd"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        data-action="折叠展开块"
      >
        <span aria-hidden>{open ? '▾' : '▸'}</span>
        <span className="fold-title">
          {open ? '推理过程' : (
            <>
              推理过程 · {teaser}
              {truncated ? '…' : ''}
            </>
          )}
        </span>
      </button>
      {open && (
        <div className="fold-bd">
          <div className="fold-text">{summary}</div>
          {steps && (
            <ol>
              {steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
