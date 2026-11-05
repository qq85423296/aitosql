import { useState } from 'react';
import { useQueryStore } from '../../stores/queryStore';

/**
 * Banners（装配五步-5 / §13）：
 * - 续播琥珀横幅（仅增量场景亮，stream_resumed 控制帧驱动，可关闭）
 * - 崩溃恢复绿横幅（D03：「已自动恢复」，无手动续跑按钮）
 * - 压缩提示条（session.compact_msgs 触发，可展开摘要）
 */
export function Banners({ compactSummary }: { compactSummary?: string | null }) {
  const resumed = useQueryStore((s) => s.resumedBanner);
  const recovered = useQueryStore((s) => s.recoveredBanner);
  const setResumed = useQueryStore((s) => s.setResumedBanner);
  const setRecovered = useQueryStore((s) => s.setRecoveredBanner);
  const [compactOpen, setCompactOpen] = useState(false);

  return (
    <>
      {resumed && (
        <div className="banner banner-amber" role="status">
          <span>连接已恢复，已从断点续播</span>
          <button className="icon-btn close" aria-label="关闭提示" data-action="关闭提示" onClick={() => setResumed(false)}>
            ×
          </button>
        </div>
      )}
      {recovered && (
        <div className="banner banner-green" role="status">
          <span>已从 checkpoint 自动续跑至完成（无手动操作）；等待门不自动放行</span>
          <button className="icon-btn close" aria-label="关闭提示" data-action="关闭提示" onClick={() => setRecovered(false)}>
            ×
          </button>
        </div>
      )}
      {compactSummary && (
        <div className="banner banner-amber" style={{ maxWidth: 760 }}>
          <button className="icon-btn" aria-expanded={compactOpen} onClick={() => setCompactOpen(!compactOpen)}>
            {compactOpen ? '▾' : '▸'}
          </button>
          <span>{compactOpen ? compactSummary : '会话较长，早期消息已压缩为摘要'}</span>
        </div>
      )}
    </>
  );
}
