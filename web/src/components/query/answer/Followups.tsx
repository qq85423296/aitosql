/** Followups（FR-Q14）：追问建议 chips；点击即触发新提问（feature.followups 开关控制显隐） */
export function Followups({
  items,
  enabled,
  onPick,
}: {
  items: string[];
  enabled: boolean;
  onPick?: (q: string) => void;
}) {
  if (!enabled || !items.length) return null;
  return (
    <div className="followups">
      {items.slice(0, 3).map((f, i) => (
        <button key={i} className="followup-chip" data-action="追问建议" onClick={() => onPick?.(f)}>
          {f}
        </button>
      ))}
    </div>
  );
}
