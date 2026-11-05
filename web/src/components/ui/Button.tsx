import { clsx } from 'clsx';
import type { ButtonHTMLAttributes } from 'react';

type Variant = 'default' | 'accent' | 'ghost' | 'amber' | 'danger' | 'ai';

export function Button({
  variant = 'default',
  size,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' }) {
  return (
    <button
      className={clsx('btn', variant !== 'default' && `btn-${variant}`, size === 'sm' && 'btn-sm', className)}
      {...rest}
    />
  );
}
