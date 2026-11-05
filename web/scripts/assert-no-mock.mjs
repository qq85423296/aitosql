/**
 * assert-no-mock.mjs —— P0-4 产物断言：生产 dist 中不得残留 mock 实现。
 * 判定：dist/assets 下所有 js 不得出现 mock 模块独有标记串「mock 未覆盖」。
 * 用法：node scripts/assert-no-mock.mjs（build 后自动执行，见 package.json build 脚本）。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../dist', import.meta.url));
const MARKERS = ['mock 未覆盖', 'mockRunPipeline'];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (name.endsWith('.js')) yield p;
  }
}

let bad = [];
try {
  for (const f of walk(dist)) {
    const text = readFileSync(f, 'utf8');
    for (const m of MARKERS) {
      if (text.includes(m)) bad.push(`${f} 含有禁入产物标记「${m}」`);
    }
  }
} catch (e) {
  if (e.code === 'ENOENT') {
    console.error(`assert-no-mock: dist 不存在（${dist}），请先构建`);
    process.exit(1);
  }
  throw e;
}

if (bad.length) {
  console.error('assert-no-mock 失败：生产产物中检出 mock 代码残留：');
  for (const b of bad) console.error(`  - ${b}`);
  process.exit(1);
}
console.log('assert-no-mock 通过：dist 中无 mock 代码残留');
