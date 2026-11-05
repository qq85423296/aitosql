/** 开关（role=switch + aria-checked，§4.1 无障碍） */
export function Switch({
  checked,
  disabled,
  onChange,
  label,
  ...rest
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
  label?: string;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'>) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="switch"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      {...rest}
    />
  );
}
