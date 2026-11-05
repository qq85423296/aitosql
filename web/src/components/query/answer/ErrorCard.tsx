import { errorSpec, R15_ASSERTIONS, type Dialect } from '../../../lib/errorText';
import { Button } from '../../ui/Button';

/**
 * ErrorCard（FR-Q13）：C8 七码表现。
 * E2001 → [重试][切换路由]；E3010 → 五断言清单（命中项高亮）+ 原 SQL 照常交付（由 SqlBlock 承载）。
 */
export function ErrorCard({
  code,
  message,
  retryable,
  dialect,
  onRetry,
  onSwitchRoute,
}: {
  code: string;
  message: string;
  retryable?: boolean;
  dialect?: Dialect;
  onRetry?: () => void;
  onSwitchRoute?: () => void;
}) {
  const spec = errorSpec(code);
  const isR15 = code === 'E3010';
  return (
    <div className="error-card" role="alert">
      <div>
        <span className="code">{spec.code}</span> <strong>{spec.title}</strong>
      </div>
      <div style={{ marginTop: 4, fontSize: 13 }}>{message}</div>
      {isR15 && (
        <ol className="assert-list">
          {R15_ASSERTIONS.map((a) => (
            <li key={a.id} className={message.includes(a.id) ? 'hit' : undefined}>
              [{a.id}] {a.text(dialect ?? 'mysql')}
            </li>
          ))}
        </ol>
      )}
      {spec.hint && <div className="proto-note" style={{ marginTop: 6 }}>{spec.hint}</div>}
      {(retryable ?? spec.retryable) && (
        <div className="actions">
          {onRetry && (
            <Button size="sm" data-action="重试生成" onClick={onRetry}>
              重试
            </Button>
          )}
          {onSwitchRoute && (
            <Button size="sm" variant="ghost" data-action="去设置切换路由" onClick={onSwitchRoute}>
              切换路由
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
