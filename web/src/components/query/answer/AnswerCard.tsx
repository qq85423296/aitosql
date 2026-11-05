/**
 * AnswerCard：库概况直通答案卡（contract.kind=meta）。
 * AI 基于已摄取元数据整理的正式回答，纯文本 pre-wrap 渲染（不用折叠头，不用等宽体）。
 */
export function AnswerCard({ text }: { text: string }) {
  return (
    <div className="answer-card">
      <div className="answer-tag">库概况 · 来自元数据</div>
      <div className="answer-body">{text}</div>
    </div>
  );
}
