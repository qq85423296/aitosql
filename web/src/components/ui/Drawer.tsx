import { clsx } from 'clsx';
import type { ReactNode } from 'react';

/** 抽屉：右侧 440px（wide=560px 组装预览；full=占满 rail 以右全宽，建议评审用）侧拉 + 遮罩；遮罩/×/取消关闭（§4.1） */
export function Drawer({
  open,
  title,
  wide,
  full,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: ReactNode;
  wide?: boolean;
  full?: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!open) return null;
  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className={clsx('drawer-panel', wide && 'wide', full && 'full')} role="dialog" aria-modal="true">
        <div className="drawer-head">
          <span>{title}</span>
          <button className="icon-btn" aria-label="关闭抽屉" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </div>
    </>
  );
}
