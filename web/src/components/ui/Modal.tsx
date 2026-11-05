import { useEffect } from 'react';
import type { ReactNode } from 'react';

/**
 * Modal：居中模态对话框（确认类操作用，如删除会话）。
 * 遮罩点击 / Esc 关闭；视觉与 Drawer 同族（bg-1 + border + 圆角），居中 400px。
 */
export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="modal-panel" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <span>{title}</span>
          <button className="icon-btn" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </div>
    </>
  );
}
