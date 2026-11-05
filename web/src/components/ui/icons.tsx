/** 原型内联 SVG 图标集（24 viewBox，stroke=currentColor；与 prototype/*.html 逐字同源） */
import type { SVGProps } from 'react';

const PATHS: Record<string, { el: 'path' | 'circle' | 'rect' | 'ellipse'; attrs: Record<string, string> }[]> = {
  plus: [{ el: 'path', attrs: { d: 'M12 5v14M5 12h14' } }],
  search: [
    { el: 'circle', attrs: { cx: '11', cy: '11', r: '7' } },
    { el: 'path', attrs: { d: 'm21 21-4.3-4.3' } },
  ],
  lock: [
    { el: 'rect', attrs: { x: '3', y: '11', width: '18', height: '11', rx: '2' } },
    { el: 'path', attrs: { d: 'M7 11V7a5 5 0 0 1 10 0v4' } },
  ],
  refresh: [{ el: 'path', attrs: { d: 'M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6' } }],
  spark: [{ el: 'path', attrs: { d: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z' } }],
  pencil: [{ el: 'path', attrs: { d: 'M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z' } }],
  eye: [
    { el: 'path', attrs: { d: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z' } },
    { el: 'circle', attrs: { cx: '12', cy: '12', r: '3' } },
  ],
  wifi: [{ el: 'path', attrs: { d: 'M5 12.55a11 11 0 0 1 14.08 0M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01' } }],
  power: [{ el: 'path', attrs: { d: 'M18.36 6.64a9 9 0 1 1-12.73 0M12 2v9' } }],
  code: [{ el: 'path', attrs: { d: 'm16 18 6-6-6-6M8 6l-6 6 6 6' } }],
  file: [
    { el: 'path', attrs: { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' } },
    { el: 'path', attrs: { d: 'M14 2v6h6' } },
  ],
  info: [
    { el: 'circle', attrs: { cx: '12', cy: '12', r: '10' } },
    { el: 'path', attrs: { d: 'M12 16v-4m0-4h.01' } },
  ],
  'check-circle': [
    { el: 'circle', attrs: { cx: '12', cy: '12', r: '10' } },
    { el: 'path', attrs: { d: 'm8 12 3 3 5-6' } },
  ],
  'alert-circle': [
    { el: 'circle', attrs: { cx: '12', cy: '12', r: '10' } },
    { el: 'path', attrs: { d: 'M12 8v4m0 4h.01' } },
  ],
  key: [
    { el: 'circle', attrs: { cx: '8', cy: '12', r: '4' } },
    { el: 'path', attrs: { d: 'M12 12h9m-3-3 3 3-3 3' } },
  ],
  grid: [{ el: 'path', attrs: { d: 'M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4', opacity: '.55' } }],
  panel: [
    { el: 'rect', attrs: { x: '3', y: '3', width: '18', height: '18', rx: '2' } },
    { el: 'path', attrs: { d: 'M9 3v18' } },
  ],
  play: [{ el: 'path', attrs: { d: 'M5 3l14 9-14 9V3z' } }],
  // rail 四页图标
  chat: [{ el: 'path', attrs: { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' } }],
  db: [
    { el: 'ellipse', attrs: { cx: '12', cy: '5', rx: '9', ry: '3' } },
    { el: 'path', attrs: { d: 'M3 5v14a9 3 0 0 0 18 0V5' } },
    { el: 'path', attrs: { d: 'M3 12a9 3 0 0 0 18 0' } },
  ],
  book: [{ el: 'path', attrs: { d: 'M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20' } }],
  gear: [
    { el: 'circle', attrs: { cx: '12', cy: '12', r: '3' } },
    { el: 'path', attrs: { d: 'M12 1v4m0 14v4M4.2 4.2l2.8 2.8m10 10 2.8 2.8M1 12h4m14 0h4M4.2 19.8l2.8-2.8m10-10 2.8-2.8' } },
  ],
};

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 14,
  strokeWidth = 1.8,
  ...rest
}: { name: IconName; size?: number; strokeWidth?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      {PATHS[name].map((p, i) =>
        p.el === 'path' ? (
          <path key={i} {...p.attrs} />
        ) : p.el === 'circle' ? (
          <circle key={i} {...p.attrs} />
        ) : p.el === 'rect' ? (
          <rect key={i} {...p.attrs} />
        ) : (
          <ellipse key={i} {...p.attrs} />
        ),
      )}
    </svg>
  );
}
