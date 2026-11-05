#!/usr/bin/env node
/**
 * V-2 闸口：从原型 assets/prototype.css 的 :root / :root[data-theme="light"]
 * 提取全部 CSS 自定义属性，与 src/theme/tokens.css 逐字比对（名称与值）。
 * 用法：node scripts/verify-tokens.mjs [prototype.css 路径]
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const protoCss =
  process.argv[2] ??
  resolve(here, '../../../02-prototype/prototype/assets/prototype.css');
const tokensCss = resolve(here, '../src/theme/tokens.css');

function extractBlock(css, selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}');
  const m = css.match(re);
  if (!m) return {};
  const map = {};
  for (const decl of m[1].split(';')) {
    const kv = decl.split(':');
    if (kv.length < 2) continue;
    const name = kv[0].trim();
    if (!name.startsWith('--')) continue;
    map[name] = kv.slice(1).join(':').trim();
  }
  return map;
}

const proto = readFileSync(protoCss, 'utf8');
const mine = readFileSync(tokensCss, 'utf8');

let fail = 0;
for (const [label, selector] of [
  ['深色', ':root'],
  ['浅色', ':root[data-theme="light"]'],
]) {
  const a = extractBlock(proto, selector);
  const b = extractBlock(mine, selector);
  const names = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const n of [...names].sort()) {
    if (!(n in a)) {
      console.error(`[多余] ${label} ${n} 仅存在于 tokens.css（值 ${b[n]}）`);
      fail++;
    } else if (!(n in b)) {
      console.error(`[缺失] ${label} ${n} 原型有而 tokens.css 无（值 ${a[n]}）`);
      fail++;
    } else if (a[n] !== b[n]) {
      console.error(`[漂移] ${label} ${n}: 原型=${a[n]} 实际=${b[n]}`);
      fail++;
    }
  }
  console.log(`${label}令牌：原型 ${Object.keys(a).length} 个，tokens.css ${Object.keys(b).length} 个`);
}

if (fail) {
  console.error(`\nV-2 闸口未通过：${fail} 处不一致`);
  process.exit(1);
}
console.log('\nV-2 闸口通过：tokens.css 与原型 :root 双块逐字一致 ✔');
