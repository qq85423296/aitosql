import { create } from 'zustand';
import { clsx } from 'clsx';

interface ToastItem {
  id: number;
  text: string;
  kind: 'info' | 'ok' | 'err';
}
interface ToastState {
  toasts: ToastItem[];
  push: (text: string, kind?: ToastItem['kind']) => void;
  remove: (id: number) => void;
}

let seq = 0;
export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (text, kind = 'info') => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 3200);
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** 全局 toast（对齐原型 app.js toast） */
export function toast(text: string, kind: ToastItem['kind'] = 'info') {
  useToastStore.getState().push(text, kind);
}

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  return (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div key={t.id} className={clsx('toast', t.kind !== 'info' && t.kind)} role="status">
          {t.text}
        </div>
      ))}
    </div>
  );
}
