/** AboutSection（FR-C8 关于，spec TODO-N6 默认假设落位） */
export function AboutSection() {
  return (
    <div className="proto-note" style={{ lineHeight: 2 }}>
      <div>
        <strong style={{ color: 'var(--t1)' }}>AItosql 个人版</strong> · Text2SQL（智能优先 · 本地部署 · 单用户）
      </div>
      <div>版本：0.1.0（SSOT v3.2 评审闭环版）</div>
      <div>数据目录：data/（app.db 终态权威 · checkpoints.db 进度权威 · backups/ · exports/）</div>
      <div>默认 local_only 零出境；凭证、完整结果集、连接地址明文绝不外发</div>
    </div>
  );
}
